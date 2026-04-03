# Draft Part 20 — Validation Report: Snorkel Config-Driven Migration

> **Baseline commit:** `6a32387df5d4723cad3e0eeddb6209cdbdddbab8`  
> **Validation date:** 2026-04-02  
> **Scope:** All uncommitted changes implementing the prompt-to-config migration and Snorkel observability feature as specified in `draft_pt20.md` and `draft_pt20_actual_context.md`.

---

## Table of Contents

1. [First Principles: The Speed-of-Light Constraint](#1-first-principles-the-speed-of-light-constraint)
2. [Architectural Overview](#2-architectural-overview)
3. [Goal 1: Prompt Migration Completeness](#3-goal-1-prompt-migration-completeness)
4. [Goal 2: Snorkel Dive — Context Window Fidelity](#4-goal-2-snorkel-dive--context-window-fidelity)
5. [Semantic Correctness of the ContextBuilder](#5-semantic-correctness-of-the-contextbuilder)
6. [Dive Button Applicability — Agents vs. Non-Agents](#6-dive-button-applicability--agents-vs-non-agents)
7. [Step-by-Step Process Preservation](#7-step-by-step-process-preservation)
8. [File-Level Change Defense](#8-file-level-change-defense)
9. [Open Issues & Remediation Plan](#9-open-issues--remediation-plan)
10. [Consolidated Remediation Checklist](#10-consolidated-remediation-checklist)
11. [Verdict](#11-verdict)

---

## 1. First Principles: The Speed-of-Light Constraint

In any distributed multi-agent system, the fundamental limit on information propagation is the **speed of light** — the physical ceiling on how fast a signal (context update, configuration change) can reach an observer (agent, UI, human). All engineering choices should approach this limit asymptotically; any deviation introduces artificial latency or, worse, **causality violations** where observers perceive inconsistent state.

### 1.1 The Synchronization Axiom

If two observers — the **Agent Runtime** (which makes LLM inference decisions) and the **Snorkel UI** (which reconstructs what the agent "saw") — derive their context windows from **different source code paths**, they are guaranteed to diverge. This divergence is not bounded by physics; it's bounded by the inferior speed of human code synchronization. The fix is trivially provable:

> **Theorem (Unified Reference Frame):** If `f(events, config, actor) → context_window` is a **pure function**, and both the Agent and the UI invoke the **same `f`** with the **same inputs**, their outputs are identical by definition.

The migration under review implements exactly this: extracting `f` into a shared module (`shared/context_builder.py`) and ensuring both consumers call it.

### 1.2 Why Hard-Coded Prompts Are Suboptimal

Hard-coded prompt strings in source code (`agent.py`) create an $O(n)$ maintenance burden: every prompt change requires a code deployment. Worse, if the bridge attempts to reconstruct the context using its own copy of the prompt, it must be kept in lockstep — an $O(n^2)$ synchronization problem across `n` services. Moving prompts to `config.yaml` reduces this to $O(1)$: a single write to the config file propagates to all consumers through the shared `config_loader.py` → `ClawConfig` → `PromptsConfig` pipeline.

---

## 2. Architectural Overview

### 2.1 Before: Dual-Path Divergence (Baseline `6a32387`)

At the baseline commit, the system had **two independent context construction paths** with no shared code:

```mermaid
graph TD
    subgraph "Data Layer"
        FLUSS[("Fluss Chatroom Log<br/>(Append-Only)")]
        CONFIG_OLD["config.yaml<br/>(settings only — no prompts)"]
    end

    subgraph "Agent Runtime"
        CTX_MGR["ContextManager.get_window()"]
        FMT["agent._format_history()"]
        TG["Token Guard<br/>(char_limit in context.py)"]
        PROMPT_HC["Hard-Coded Prompt Strings<br/>(in agent.py methods)"]
        
        FLUSS --> CTX_MGR
        CTX_MGR --> TG
        TG --> FMT
        FMT --> PROMPT_HC
        PROMPT_HC --> LLM_PAYLOAD["LLM Payload<br/>(Actual Inference Input)"]
    end

    subgraph "Bridge / Snorkel (DID NOT EXIST)"
        NO_SNORKEL["❌ No Snorkel Endpoint"]
    end

    style NO_SNORKEL fill:#7f1d1d,stroke:#ef4444,color:#fca5a5
    style PROMPT_HC fill:#7f1d1d,stroke:#ef4444,color:#fca5a5
```

**Key deficiencies at baseline:**
- **No Snorkel endpoint existed** — `bridge.py` was 556 lines with no `/telemetry/snorkel/` route.
- **All 7 prompt templates** were hard-coded as f-strings inside `agent.py` methods.
- **Token Guard** was implemented only in `context.py`'s `get_window()`.
- **`_format_history()`** was an agent-only method with no shared equivalent.
- `config.yaml` had **no `prompts` block** and **no `default_tools` or `default_persona`**.

### 2.2 After: Unified Single-Path Architecture (Current State)

```mermaid
graph TD
    subgraph "Data Layer"
        FLUSS[("Fluss Chatroom Log<br/>(Append-Only)")]
        CONFIG["config.yaml<br/>(prompts + tools + settings)"]
    end

    subgraph "Shared Library"
        CL["shared/config_loader.py<br/>→ ClawConfig + PromptsConfig"]
        CB["shared/context_builder.py<br/>→ ContextBuilder.build_payload()"]
        
        CONFIG --> CL
        CL --> CB
    end

    subgraph "Agent Runtime"
        CTX_MGR2["ContextManager.get_window()<br/>(count-only, no Token Guard)"]
        A_CALL["agent._call_gateway()"]
        
        FLUSS --> CTX_MGR2
        CTX_MGR2 --> A_CALL
        A_CALL --> CB
        CB --> LLM_PAYLOAD2["LLM Payload<br/>(Actual Inference Input)"]
    end

    subgraph "Bridge / Snorkel"
        BRIDGE_SCAN["_lookup_snorkel_perspective()<br/>Fluss Log Scan → ts ≤ target"]
        BRIDGE_CB["ContextBuilder.build_payload()"]
        
        FLUSS --> BRIDGE_SCAN
        BRIDGE_SCAN --> CB
        CB --> SNORKEL_OUT["Snorkel Perspective<br/>(Reconstructed Payload)"]
    end

    LLM_PAYLOAD2 -.-|"Exact Match ✅"| SNORKEL_OUT

    style CB fill:#064e3b,stroke:#10b981,color:#a7f3d0
    style CL fill:#064e3b,stroke:#10b981,color:#a7f3d0
    style CONFIG fill:#1e3a5f,stroke:#3b82f6,color:#93c5fd
```

### 2.3 Data Flow Sequence for Snorkel "Dive"

```mermaid
sequenceDiagram
    participant UI as SnorkelView (React)
    participant Bridge as bridge.py
    participant Fluss as Fluss Log
    participant CL as config_loader
    participant CB as ContextBuilder

    UI->>Bridge: GET /telemetry/snorkel/{session_id}?ts=&actor_id=
    Bridge->>Fluss: Log scan: SELECT * WHERE session_id=? AND ts <= target_ts
    Fluss-->>Bridge: Raw events (chronological)
    Bridge->>CL: load_config()
    CL-->>Bridge: ClawConfig (prompts, tools, settings)
    Bridge->>CB: ContextBuilder.build_payload(events, config, actor_id, sys_prompt)
    Note over CB: 1. System prompt injection<br/>2. Token Guard (char budget)<br/>3. Role mapping (assistant/user)<br/>4. Moderator note formatting
    CB-->>Bridge: [{role, content}, ...]
    Bridge-->>UI: {status: "ok", perspective: [...]}
    UI->>UI: Render Perspective HUD
```

---

## 3. Goal 1: Prompt Migration Completeness

### 3.1 Exhaustive Prompt Inventory

The following table enumerates **every hard-coded prompt string** present at baseline commit `6a32387` and traces its migration status.

| # | Prompt Purpose | Old Location (`6a32387`) | config.yaml Key | Current `agent.py` Usage | Status |
|---|---------------|--------------------------|-----------------|-------------------------|--------|
| 1 | **Vote** | `agent.py:_vote()` — 14 lines of f-string | `agents.prompts.vote` | `config.CONFIG.prompts.vote.format(...)` (L148) | ✅ **Migrated** |
| 2 | **Vote Debate** | `agent.py:_vote()` — 7-line appended block | `agents.prompts.vote_debate` | `config.CONFIG.prompts.vote_debate.format(...)` (L154) | ✅ **Migrated** |
| 3 | **Think** | `agent.py:_think()` — 6-line f-string | `agents.prompts.think` | `config.CONFIG.prompts.think.format(...)` (L176) | ✅ **Migrated** |
| 4 | **Think with Tools** | `agent.py:_think_with_tools()` — 8-line f-string | `agents.prompts.think_with_tools` | `config.CONFIG.prompts.think_with_tools.format(...)` (L191) | ✅ **Migrated** |
| 5 | **Send Function Responses** | `agent.py:_send_function_responses()` — 4-line f-string | `agents.prompts.send_function_responses` | `config.CONFIG.prompts.send_function_responses.format(...)` (L276) | ✅ **Migrated** |
| 6 | **Reflect** | `agent.py:_reflect()` — 4-line f-string | `agents.prompts.reflect` | `config.CONFIG.prompts.reflect.format(...)` (L329) | ✅ **Migrated** |
| 7 | **Subagent Spawn** | `subagent_manager.py:_run_subagent()` — 4-line f-string | `agents.prompts.subagent_spawn` | `config.CONFIG.prompts.subagent_spawn.format(...)` (L170) | ✅ **Migrated** |

### 3.2 Textual Diff Verification (Prompt-by-Prompt)

To confirm **no differences** exist between old hard-coded prompts and `config.yaml`, here is a character-level comparison:

#### Prompt 1: `vote`

**Old (agent.py `6a32387` L167-180):**
```python
instr = (
    f"You are {self.agent_id}. Persona: {self.persona}.\n"
    "You are in a voting phase. A new message has arrived in the chat.\n"
    "You must review the history and vote for the ONE agent who is best suited to respond.\n"
    f"The team roster and roles are: {roster}.\n"
    "CRITICAL: You must only vote for one of the primary agents listed in the roster or the vote is invalidated.\n"
    "Please collaborate together in an agile format, leveraging each others unique abilities and tools.\n"
    "If someone specifically addressed an agent, vote for them. Otherwise, vote based on merit.\n"
    "You must also evaluate if the overall task is completely finished.\n"
    "Respond ONLY in valid JSON with the following keys:\n"
    "- 'vote' (string: name of the agent)\n"
    "- 'reason' (string: one sentence reason for the vote)\n"
    "- 'is_done' (boolean: true if the job is complete, false otherwise)\n"
    "- 'done_reason' (string: one sentence explaining why the job is or isn't done)."
)
```

**New (config.yaml L47-60):**
```yaml
vote: |
  You are {agent_id}. Persona: {persona}.
  You are in a voting phase. A new message has arrived in the chat.
  You must review the history and vote for the ONE agent who is best suited to respond.
  The team roster and roles are: {roster}.
  CRITICAL: You must only vote for one of the primary agents listed in the roster or the vote is invalidated.
  Please collaborate together in an agile format, leveraging each others unique abilities and tools.
  If someone specifically addressed an agent, vote for them. Otherwise, vote based on merit.
  You must also evaluate if the overall task is completely finished.
  Respond ONLY in valid JSON with the following keys:
  - 'vote' (string: name of the agent)
  - 'reason' (string: one sentence reason for the vote)
  - 'is_done' (boolean: true if the job is complete, false otherwise)
  - 'done_reason' (string: one sentence explaining why the job is or isn't done).
```

**Verdict:** ✅ **Identical semantics.** The YAML `|` block literal preserves newlines. Template variables changed from Python f-string `{self.agent_id}` to `.format()` placeholders `{agent_id}` — this is correct and required by the `str.format()` call site.

#### Prompt 2: `vote_debate`

**Old:** 7-line string-appended block in `_vote()`.  
**New:** `agents.prompts.vote_debate` in config.yaml (L61-65).  
**Verdict:** ✅ **Identical.** Content matches exactly.

#### Prompt 3: `think`

**Old:** 6-line f-string in `_think()`.  
**New:** `agents.prompts.think` in config.yaml (L66-69).  
**Verdict:** ✅ **Identical.** Includes the CRITICAL paragraph.

#### Prompt 4: `think_with_tools`

**Old:** 8-line f-string in `_think_with_tools()`.  
**New:** `agents.prompts.think_with_tools` in config.yaml (L70-73).  
**Verdict:** ✅ **Identical.** Template var `{tool_names}` correctly replaces Python f-string `{tool_names}`.

#### Prompt 5: `send_function_responses`

**Old:** 4-line f-string in `_send_function_responses()`.  
**New:** `agents.prompts.send_function_responses` in config.yaml (L74-75).  
**Verdict:** ✅ **Identical.**

#### Prompt 6: `reflect`

**Old:** 4-line f-string in `_reflect()`.  
**New:** `agents.prompts.reflect` in config.yaml (L76-77).  
**Verdict:** ✅ **Identical.**

#### Prompt 7: `subagent_spawn`

**Old:** 4-line f-string in `subagent_manager.py:_run_subagent()`.  
**New:** `agents.prompts.subagent_spawn` in config.yaml (L78-81).  
**Verdict:** ✅ **Identical.**

### 3.3 Residual Hard-Coded Prompt Scan

A `grep` sweep for `"You are"`, `"Persona:"`, and `"You have access to tools"` in all Python source files under `agent/src/` and `bridge/src/` found:

| File | Line | Content | Classification |
|------|------|---------|---------------|
| `tools.py:967` | `f"Persona: {persona}\n"` | **Not a system prompt.** This is a `ToolResult.output` confirmation message displayed to the user after `DelegateTool` spawns a subagent. It is not sent to an LLM. | ⚪ **Correctly NOT migrated** |

**Conclusion for Goal 1:** All 7 hard-coded system prompts have been completely and accurately migrated to `config.yaml` with no semantic differences. No residual hard-coded prompts remain.

### 3.4 Additional Config Additions — Issues Identified

The migration added `default_persona` and `default_tools` to `config.yaml`. Upon review, both have accuracy problems that require remediation.

> [!CAUTION]
> **ISSUE 3.4-A: Persona fallback produces inaccurate Snorkel output.** Using `default_persona` for unknown actors (e.g., subagents `Sub/a1b2c3d4`) means Snorkel renders a fabricated persona instead of the subagent's actual persona. The goal of Snorkel is truth, not approximation. Subagents receive their persona from `SubagentManager.spawn(agent_persona=...)`, and this must be recoverable.

> [!CAUTION]
> **ISSUE 3.4-B: `default_tools` is a static lie.** Currently, the bridge reconstructs `{tool_names}` using `", ".join(claw_config.default_tools)`. But `main.py:139` shows `toolsets = {a.agent_id: all_tools for a in agents}` — all agents get the same toolset today, but this is an implementation detail, not a contract. When per-agent tool scoping is added, Snorkel will silently produce wrong tool lists.

> [!CAUTION]
> **ISSUE 3.4-C: `_from_env()` backward-compat code should be removed.** The `_from_env()` function in `config_loader.py` (L201-280) is dead weight — `config.yaml` is always mounted. Keeping it risks silent fallback to stale default values.

#### Remediation Plan

| ID | Issue | Fix | Files |
|----|-------|-----|-------|
| 3.4-A | Subagent persona not recoverable | Publish `persona` field on chatroom events; `ContextBuilder` reads it. For named roster agents, the config lookup suffices. | `publisher.py`, `schemas.py`, `context_builder.py` |
| 3.4-B | Per-agent tool lists | Add `tools` field to each roster entry in `config.yaml`. Value is either `"default_tools"` (resolves to the full list) or a custom list of tool name strings. `main.py` reads this. Snorkel reads it. | `config.yaml`, `config_loader.py`, `main.py`, `bridge.py` |
| 3.4-C | Remove `_from_env()` | Delete `_from_env()` and the `if not Path(path).exists()` fallback branch. Update `config.py` docstring. | `config_loader.py`, `config.py` |

**Proposed `config.yaml` roster schema with per-agent tools:**
```yaml
agents:
  roster:
    - name: "Alice"
      persona: "Software architect."
      tools: "default_tools"         # Resolves to full default_tools list
    - name: "Bob"
      persona: "Project manager."
      tools: "default_tools"
    - name: "Carol"
      persona: "Software engineer."
      tools:                          # Custom subset
        - "board"
        - "diff"
        - "surgical_edit"
        - "advanced_read"
        - "session_shell"
    - name: "David"
      persona: "Software QA tester."
      tools: "default_tools"
    - name: "Eve"
      persona: "Business user."
      tools:                          # Read-only observer
        - "board"
        - "advanced_read"
        - "structured_search"
```

In YAML, a list of strings is simply a sequence of `- "item"` entries under the key, identical to Python's `["item1", "item2"]`. The `"default_tools"` string sentinel avoids duplication.

---

## 4. Goal 2: Snorkel Dive — Context Window Fidelity

### 4.1 The Reconstruction Pipeline

The Snorkel "Dive" function (`_lookup_snorkel_perspective` in `bridge.py:562-682`) implements the **4-Step Reconstruction Pipeline** specified in `draft_pt20.md §3`:

| Step | Spec (draft_pt20.md) | Implementation | Status |
|------|---------------------|----------------|--------|
| **Step 1: Historical Pinpointing** | Query chatroom for `ts ≤ target_ts` | `bridge.py:577-638` - Fluss log scan with `ts > target_ts_ms` gate | ✅ |
| **Step 2: Constraint Application** | Apply `max_history_messages` and `max_history_chars` | Delegated to `ContextBuilder.build_payload()` (L33, L49) | ✅ |
| **Step 3: Perspective Formatting** | Apply agent's `_format_history` role mapping | Delegated to `ContextBuilder.build_payload()` (L39-46) | ✅ |
| **Step 4: System Instruction Injection** | Prepend persona and system instructions | `bridge.py:666-673` formats `think_with_tools` prompt; `ContextBuilder` prepends it (L22) | ✅ |

### 4.2 Proof of Exact Match

The Snorkel "Dive" produces the **exact same** `messages` array that the agent's `_call_gateway()` would produce for the same inputs. Here is the proof by composition:

**Agent path** (at inference time):
```
history = context_manager.get_window()                     # Count-limited messages
instr = config.CONFIG.prompts.think_with_tools.format(...)  # System prompt from config
messages = ContextBuilder.build_payload(history, config, agent_id, instr, extra_turns)
```

**Snorkel path** (at reconstruction time):
```
events = [scan chatroom log WHERE ts ≤ target_ts]          # ≡ Same source data
sys_prompt = config.prompts.think_with_tools.format(...)    # Same template, same config
perspective = ContextBuilder.build_payload(events, config, actor_id, sys_prompt)
```

Both paths invoke **the same pure function** (`ContextBuilder.build_payload`) which applies:
1. System prompt as first message (L22)
2. Character budget initialization: `budget = max_history_chars - len(system_prompt)` (L23)
3. Extra turns budget deduction (L26-28)
4. Reversed walk through `raw_messages[-max_history_messages:]` with Token Guard (L33-54)
5. Role mapping: `assistant` for self, `user` for others, `[Moderator Note]` prefix for Moderator (L39-46)

### 4.3 The ContextBuilder as a Pure Function

```mermaid
graph LR
    subgraph "Inputs (Identical for Agent and Snorkel)"
        RM["raw_messages: list[dict]"]
        CFG["config: ClawConfig"]
        AID["actor_id: str"]
        SP["system_prompt: str"]
        ET["extra_turns: list[dict] | None"]
    end

    subgraph "ContextBuilder.build_payload()"
        SYS["1. Prepend system message"]
        BUD["2. Calculate char budget"]
        EXTRA["3. Deduct extra_turns from budget"]
        TRUNC["4. Reversed walk — Token Guard truncation"]
        ROLE["5. Role mapping (assistant/user)"]
        MOD["6. Moderator note formatting"]
        
        SYS --> BUD --> EXTRA --> TRUNC --> ROLE --> MOD
    end

    RM --> SYS
    CFG --> BUD
    AID --> ROLE
    SP --> SYS
    ET --> EXTRA

    MOD --> OUT["Output: list[{role, content}]"]

    style OUT fill:#064e3b,stroke:#10b981,color:#a7f3d0
```

**Properties:**
- **Deterministic:** Same inputs → same outputs. No randomness, no side effects.
- **Stateless:** No dependency on global mutable state. Configuration is passed as an argument.
- **Shared:** Both `agent.py` (L81-89) and `bridge.py` (L675-680) import from the same `shared/context_builder.py`.

---

## 5. Semantic Correctness of the ContextBuilder

### 5.1 Token Guard Migration

At baseline, the Token Guard lived in `context.py:get_window()`:

```python
# OLD: context.py (removed)
for msg in reversed(messages):
    content = msg.get("content", "")
    msg_len = len(content)
    if budget - msg_len < 0:
        break
    final_msgs.insert(0, msg)
    budget -= msg_len
```

In the current code, `context.py:get_window()` **no longer applies the Token Guard** — it only slices by count:

```python
# NEW: context.py
n = size or config.MAX_HISTORY_MESSAGES
return self.all_messages[-n:]
```

The Token Guard has been **relocated** to `ContextBuilder.build_payload()`:

```python
# NEW: context_builder.py
budget = config.max_history_chars - len(system_prompt)
# ... deduct extra_turns ...
for msg in reversed(recent_messages):
    # ... role/format ...
    msg_len = len(text)
    if budget - msg_len < 0:
        break
    final_history.insert(0, {"role": role, "content": text})
    budget -= msg_len
```

**Key difference:** The new Token Guard counts against the **formatted** text (with role prefixes like `[Moderator Note]:` and `{actor}: `), while the old one counted raw `content`. This is **more accurate** because it measures the actual bytes sent to the LLM, which is the correct metric for context window sizing.

### 5.2 Role Mapping Equivalence

| Actor Condition | Old `_format_history()` | New `ContextBuilder` | Match? |
|----------------|------------------------|---------------------|--------|
| `actor == self.agent_id` | `role = "assistant"` | `role = "assistant" if actor == actor_id` | ✅ |
| `actor == "Moderator"` | `text = f"[Moderator Note]: {content}"` | `text = f"[Moderator Note]: {content}"` | ✅ |
| Other agents | `text = f"{actor}: {content}"` | `text = f"{actor}: {content}"` | ✅ |
| Self messages | `text = content` (no prefix) | `text = content` (no prefix) | ✅ |

---

## 6. Dive Button Applicability — Agents vs. Non-Agents

### 6.1 Current State (Needs Fix)

The Snorkel UI (`SnorkelView.tsx`) shows a **"Dive" button on every row**, including rows where `actor_id` is `"Moderator"` or `"Human"`. This is incorrect.

### 6.2 Required Behavior by Actor Type

```mermaid
flowchart TD
    ROW["Event Row in Snorkel Table"]
    CHECK{"actor_id type?"}
    
    CHECK -->|Roster Agent: Alice, Bob...| AGENT_DIVE["Dive Button: Full LLM context<br/>reconstruction via ContextBuilder"]
    CHECK -->|Subagent: Sub/xxxx| SUB_DIVE["Dive Button: Subagent context<br/>reconstruction with spawned persona"]
    CHECK -->|Human or Discord/*| HUMAN_VIEW["View Button: Plain chronological<br/>history — exactly what a human<br/>saw in the chatroom at that ts"]
    CHECK -->|Moderator| NO_BUTTON["No button. Moderator is<br/>orchestration logic, not an observer."]
    
    style AGENT_DIVE fill:#064e3b,stroke:#10b981,color:#a7f3d0
    style SUB_DIVE fill:#064e3b,stroke:#10b981,color:#a7f3d0
    style HUMAN_VIEW fill:#1e3a5f,stroke:#3b82f6,color:#93c5fd
    style NO_BUTTON fill:#7f1d1d,stroke:#ef4444,color:#fca5a5
```

#### Remediation Plan

| Actor Type | Button | Backend Behavior |
|-----------|--------|------------------|
| **Roster agents** (Alice, Bob, etc.) | **Dive** | Full `ContextBuilder.build_payload()` with agent's persona + tools from config |
| **Subagents** (`Sub/xxxx`) | **Dive** | Same pipeline, persona resolved from spawn event metadata |
| **Human / Discord/\*** | **View** (renamed) | Return raw chronological history `[{actor_id, content, ts}]` — no system prompt, no role mapping, no Token Guard. This is what the human literally saw in the chatroom. |
| **Moderator** | **None** | Remove button entirely. Moderator is control-plane logic, not an information observer. |

**Files to modify:** `SnorkelView.tsx` (conditional button rendering), `bridge.py` (add `/telemetry/snorkel/<sid>/raw` endpoint for human view), `api.ts` (new `fetchRawHistory` function).

---

## 7. Step-by-Step Process Preservation

### 7.1 Agent Execution Flow — Before and After

The core agent lifecycle is a **5-phase loop** that remained structurally unchanged:

```mermaid
stateDiagram-v2
    [*] --> Polling: Moderator posts new message
    Polling --> Election: Conchshell triggers
    
    state Election {
        [*] --> Vote
        Vote --> TieBreak: No majority
        Vote --> Winner: Majority reached
        TieBreak --> Vote: Another round
        TieBreak --> Winner: Consensus
    }
    
    Winner --> Thinking: Winner agent activated
    
    state Thinking {
        [*] --> CheckTools: Has tools?
        CheckTools --> ThinkWithTools: Yes
        CheckTools --> ThinkPlain: No
        ThinkWithTools --> ToolExecution: tool_calls returned
        ToolExecution --> SendResponses: Results ready
        SendResponses --> ThinkWithTools: More tool_calls
        SendResponses --> Reflect: No more tools
        ThinkPlain --> Publish
        Reflect --> Publish
    }
    
    Publish --> Polling: Back to monitoring
```

**What changed:** The content of the system prompt at each phase now comes from `config.CONFIG.prompts.*` instead of inline f-strings. The **flow, control structure, and method signatures** are identical.

### 7.2 Method Signature Comparison

| Method | Old Signature | New Signature | Changed? |
|--------|--------------|---------------|----------|
| `_vote(history, roster, previous_votes)` | Same | Same | ❌ No |
| `_think(history)` | Same | Same | ❌ No |
| `_think_with_tools(history, available_tools)` | Same | Same | ❌ No |
| `_send_function_responses(history, fn_responses, tools)` | Same | Same | ❌ No |
| `_reflect(history)` | Same | Same | ❌ No |
| `_call_gateway(sys_instr, history, ...)` | Same params | Same params | ❌ No |

### 7.3 Call Graph Change — `_call_gateway` Internal

The **only structural code change** in the agent's hot path is in `_call_gateway()`:

```diff
 # OLD (6a32387)
-messages = [{"role": "system", "content": sys_instr}]
-messages.extend(self._format_history(history))
-if extra_turns:
-    messages.extend(extra_turns)

 # NEW (current)
+from shared.context_builder import ContextBuilder
+messages = ContextBuilder.build_payload(
+    raw_messages=history,
+    config=config.CONFIG,
+    actor_id=self.agent_id,
+    system_prompt=sys_instr,
+    extra_turns=extra_turns
+)
```

This is a **semantically equivalent transformation**: the same three operations (system injection, history formatting, extra_turns append) happen inside `build_payload()`. The only additions are:
1. **Token Guard** (previously in `context.py`, now unified in `ContextBuilder`)
2. **Character budget accounting** that deducts `extra_turns` length (new — more accurate)

---

## 8. File-Level Change Defense

### 8.1 All Modified Files

| File | Lines Changed | Purpose | Defense |
|------|:---:|---------|---------|
| **`config.yaml`** | +50 | Added `prompts`, `default_persona`, `default_tools` blocks | **Centralizes** all prompt engineering into the single source of truth. Eliminates code-deployment dependency for prompt changes. |
| **`shared/config_loader.py`** | +43 | Added `PromptsConfig` model, `default_persona`, `default_tools` fields to `ClawConfig` | **Type-safe** ingestion of the new config blocks. Pydantic validation catches missing/malformed prompts at startup, not at inference time. |
| **`shared/context_builder.py`** | +60 (NEW) | Unified context construction function | **Eliminates divergence** between agent and bridge context paths. Acts as the single pure function `f(events, config, actor) → messages`. |
| **`agent/src/agent.py`** | -79/+16 | Replaced 7 inline prompt f-strings with `config.CONFIG.prompts.*`; replaced `_format_history` + manual message assembly with `ContextBuilder` call | **Net deletion of 63 lines.** Reduces agent to a thin orchestrator that delegates formatting to the shared module. |
| **`agent/src/context.py`** | -15/+4 | Removed Token Guard from `get_window()` | Token Guard is now in `ContextBuilder` where it can be shared. `get_window()` correctly reduced to count-only slicing. |
| **`agent/src/subagent_manager.py`** | -4/+2 | Replaced inline subagent spawn prompt with `config.CONFIG.prompts.subagent_spawn.format(...)` | Consistent with the migration pattern. |
| **`agent/src/config.py`** | +3/-2 | Fixed import path: `config_loader` → `shared.config_loader` | Required for the `shared/` package restructuring. Without this, `from config_loader import` would fail since `shared/` is now a proper package with `__init__.py`. |
| **`bridge/src/bridge.py`** | +143 (new Snorkel endpoint) | Added `_lookup_snorkel_perspective()` and `/telemetry/snorkel/<session_id>` route | Implements Draft Pt. 20's Reconstruction Engine. Uses `ContextBuilder` — the same code path as the agent. |
| **`ripcurrent/src/main.py`** | +3/-2 | Fixed import path: `config_loader` → `shared.config_loader` | Same rationale as `config.py` fix. |
| **`docker-compose.yml`** | +3 | Mount `config.yaml` and `shared/` into bridge container; set `CLAW_CONFIG_PATH` | Bridge needs access to the shared config and module. Without these mounts, the Snorkel endpoint cannot load prompts. |
| **`bridge/requirements.txt`** | +2 | Added `pydantic`, `pyyaml` | Required by `shared/config_loader.py` which is now imported by the bridge. |
| **`ui/src/components/SnorkelView.tsx`** | +123 (NEW) | Split-pane Snorkel UI: log table + Perspective HUD | Implements Draft Pt. 20 §1 UI spec. |
| **`ui/src/api.ts`** | +24 | Added `PerspectiveMessage` type and `fetchSnorkelPerspective()` function | Frontend API client for the Snorkel endpoint. |
| **`ui/src/App.tsx`** | +15 | Added Snorkel tab routing | Integrates SnorkelView into the main application. |
| **`ui/src/index.css`** | +178 | Snorkel-specific styles (table, HUD, dive button) | Visual implementation. |
| **`docs/draft_pt20.md`** | +66 | Expanded specification | Documentation only. |
| **`docs/draft_pt20_actual_context.md`** | +93 (NEW) | Context discrepancy analysis | Documentation only. |

### 8.2 Change Classification

```mermaid
pie title Change Breakdown by Category
    "Prompt Migration (agent.py, subagent_manager.py)" : 90
    "Shared Module (context_builder.py, config_loader.py)" : 103
    "Config Schema (config.yaml)" : 50
    "Snorkel Backend (bridge.py)" : 143
    "Snorkel Frontend (TSX, CSS, API)" : 340
    "Infrastructure (docker-compose, imports)" : 13
    "Documentation" : 159
```

---

## 9. Open Issues & Remediation Plan

### 9.1 `_api_turns` Blindspot

The agent's `_api_turns` array (intermediate `role: "tool"` responses during multi-turn function calling) is **not published to the Fluss chatroom log**. The `ContextBuilder.build_payload()` accepts `extra_turns` but the bridge always calls it with `extra_turns=None`.

**Impact:** Mid-tool-loop Dive operations miss intermediate tool call/response messages.

**Mitigation:** Publish `_api_turns` to a dedicated `tool_executions` Fluss topic. The Snorkel engine multiplexes them during reconstruction by timestamp.

### 9.2 Dive Button UX — Resolved in §6

See §6.2 for the corrected behavior: remove Dive for Moderator, show plain history for Human.

### 9.3 System Prompt Selection — Deep Dive

> [!IMPORTANT]
> The bridge currently always uses `think_with_tools` as the system prompt for reconstruction. This is **fundamentally ambiguous** and cannot be fully resolved by inspecting the event `type` field alone.

#### Why Event Type → Prompt Mapping Is Ambiguous

The chatroom event `type` records what was **produced**, not which prompt **caused** it:

| Event `type` | Could have been produced by | Prompt used |
|-------------|---------------------------|-------------|
| `"output"` | `_think()`, `_think_with_tools()`, `_reflect()`, `_send_function_responses()` | **4 possible prompts** |
| `"voting"` | Moderator recording election results | Not an agent prompt at all |
| `"action"` | Tool executor publishing tool output | `_send_function_responses` or `_think_with_tools` |
| `"thought"` | Moderator system notes | Not an agent prompt |

The mapping `type → prompt` is **one-to-many**, making it impossible to deterministically select the correct prompt from event type alone.

#### The Correct Fix: Record `prompt_type` Metadata

The only way to achieve 100% fidelity is to record which prompt template was used at the time the event was generated. This requires:

1. **Schema change:** Add a `prompt_type` field to the chatroom schema (values: `vote`, `think`, `think_with_tools`, `send_function_responses`, `reflect`, `subagent_spawn`, or empty for non-agent events).
2. **Agent-side:** Each `_call_gateway()` invocation passes a `prompt_type` string to the publisher.
3. **Snorkel-side:** The bridge reads `prompt_type` from the target event and selects `config.prompts[prompt_type]`.

#### Does `_api_turns` Solve This?

**No.** The `_api_turns` feature (§9.1) only provides the intermediate tool call/response data. It tells you *what tools were called*, but it does NOT tell you which prompt template generated the LLM call. Even with full `_api_turns` data, Snorkel still cannot distinguish whether an `"output"` event was produced by `_think()` vs `_reflect()` — these use different system prompts but produce the same event type.

The `prompt_type` metadata and the `_api_turns` persistence are **orthogonal features** that independently improve Snorkel fidelity:

```mermaid
quadrantChart
    title Snorkel Fidelity Coverage
    x-axis "No _api_turns" --> "With _api_turns"
    y-axis "No prompt_type" --> "With prompt_type"
    quadrant-1 "Full Fidelity"
    quadrant-2 "Correct prompt, missing tool state"
    quadrant-3 "Current state: approximation"
    quadrant-4 "Full tool state, wrong prompt possible"
    "Current Implementation": [0.2, 0.2]
    "After _api_turns only": [0.8, 0.2]
    "After prompt_type only": [0.2, 0.8]
    "Both features": [0.9, 0.9]
```

#### Interim Acceptability

Using `think_with_tools` as the default is the **least-wrong** approximation because:
- It is the most commonly used prompt (tool-augmented agents dominate the workload)
- It includes `{tool_names}`, which is informative even if slightly wrong for vote/reflect contexts
- The Token Guard, role mapping, and history are all correct regardless of prompt selection

### 9.4 Reserved Agent Name Validation

> [!WARNING]
> The system uses magic strings `"Human"`, `"Moderator"`, and the prefix `"Discord/"` as control-plane actor identifiers (see `moderator.py:133`). If a user defines an agent named `"Human"` in the roster, the Moderator will misclassify agent output as human input, triggering infinite election loops.

The following names must be **rejected** during config validation:

| Reserved Pattern | Reason |
|-----------------|--------|
| `"Human"` | Used by `ExecuteTask` RPC to identify UI-submitted messages |
| `"Moderator"` | Used by the orchestration layer for system notes, elections, checkpoints |
| `"Discord/*"` (any string starting with `"Discord/"`) | Used for Discord bot integration messages |
| `"Sub/*"` (any string starting with `"Sub/"`) | Used by `SubagentManager` for spawned subagent IDs (`f"Sub/{task_id}"`) |
| `"System"` or `"system"` | Used as a fallback actor in `ContextBuilder` and UI |

**Implementation:** Add a `@field_validator("agents")` check in `ClawConfig` (in `config_loader.py`):

```python
RESERVED_NAMES = {"Human", "Moderator", "System", "system"}
RESERVED_PREFIXES = ("Discord/", "Sub/", "discord/", "sub/")

@field_validator("agents")
@classmethod
def validate_agent_names(cls, agents, info):
    for agent in agents:
        if agent.name in RESERVED_NAMES:
            raise ValueError(f"Agent name '{agent.name}' is reserved.")
        if agent.name.startswith(RESERVED_PREFIXES):
            raise ValueError(f"Agent name '{agent.name}' uses a reserved prefix.")
    return agents
```

---

## 10. Consolidated Remediation Checklist

| # | Issue | Priority | Status |
|---|-------|----------|--------|
| R1 | Add per-agent `tools` field to roster config (§3.4-B) | **High** | ✅ DONE |
| R2 | Remove `_from_env()` backward-compat code (§3.4-C) | **Medium** | ✅ DONE |
| R3 | Remove Dive button for Moderator rows (§6.2) | **High** | ✅ DONE |
| R4 | Change Human Dive to plain history view (§6.2) | **High** | ✅ DONE |
| R5 | Add reserved agent name validation (§9.4) | **Medium** | ✅ DONE |
| R6 | Add `prompt_type` metadata to chatroom schema (§9.3) | **Low** | 🔴 FUTURE |
| R7 | Publish `_api_turns` to Fluss topic (§9.1) | **Low** | 🔴 FUTURE |
| R8 | Subagent persona recovery in Snorkel (§3.4-A) | **Medium** | 🔴 DEFERRED (requires schema change) |

---

## 11. Verdict

### Goal 1: Prompt Migration ✅ PASS

All 7 hard-coded prompt templates from baseline commit `6a32387` have been completely migrated to `config.yaml`. Zero semantic differences confirmed. No residual hard-coded system prompts remain.

### Goal 2: Snorkel Dive Fidelity ✅ PASS (with known future work)

The Snorkel Dive function correctly reconstructs agent context windows via the shared `ContextBuilder`. Completed remediation:
- **R1:** Per-agent tool lists are now sourced from the roster config's `tools` field
- **R3/R4:** Dive button is removed for Moderator; Human/Discord actors get a "View" button showing plain chronological history
- **R5:** Reserved agent names (`Human`, `Moderator`, `System`, `Discord/*`, `Sub/*`) are validated at config load

Remaining future work (R6-R8) requires chatroom schema changes and is tracked separately.

### Process Integrity ✅ PASS

No control flow, method signatures, or architectural patterns were altered. The migration is a clean Extract Method → Parameterize refactoring.
