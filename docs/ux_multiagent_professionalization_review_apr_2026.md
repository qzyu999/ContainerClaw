# UX + Multi-Agent Professionalization Review (April 30, 2026)

## Why this review exists
This note is a practical PTAL for pushing ContainerClaw toward:
1. **Apple-level clarity/fit-and-finish** for mixed user demographics.
2. **Truer multi-agent swarm behavior** beyond one-agent-at-a-time + opportunistic parallelism.
3. **Bench-ready scientific framing** for SWE-bench-verified + EnLIghTenED paper claims.

---

## Current strengths worth preserving

- Strong architectural separation of concerns (agent, gateway, bridge, telemetry, UI) with a security-first framing in project docs.
- Existing UI already exposes rich surfaces (chatroom, DAG, metrics, anchor, snorkel, explorer, board), which is a strong foundation.
- Subagent lifecycle infrastructure already exists (`SubagentManager`) with spawn/cancel/status semantics and event chaining hooks.

---

## UX gaps currently preventing “professional” feel

### 1) Information hierarchy is feature-rich but role-poor
You have many panes/tabs, but no clear **persona modes** (Beginner / Operator / Investigator / Enterprise Auditor). New users face too much simultaneous choice.

**Proposal:** Introduce role-based workspace presets.
- **Beginner mode:** Chat + simple status + “what’s happening now” timeline.
- **Average user mode:** Adds board + explorer.
- **Prosumer mode:** Adds DAG + metrics.
- **Enterprise mode:** Adds policy/audit timeline, export, SLO panels.

### 2) Session creation is technical before task-centric
Current flow emphasizes runtime options early. For non-experts, first question should be intent (“What are we trying to do?”), then infer sane runtime defaults.

**Proposal:** task-first “New Session” wizard.
- Step 1: goal template (bugfix, feature, incident, eval run, research task).
- Step 2: risk profile (safe/default/aggressive).
- Step 3: optional advanced runtime overrides.

### 3) Status semantics are too coarse for trust
`Idle/Thinking/Executing/Responding` is useful but not enough for confidence at scale.

**Proposal:** add progress state model:
- planning
- delegating
- executing
- waiting_on_tool
- reconciling
- completed/blocked

Expose elapsed time + confidence + blocker reason.

### 4) “Professional polish” details
- Inconsistent visual language (emoji-rich in some areas, utilitarian labels in others).
- Lack of keyboard-first interactions and command palette.
- Limited empty/loading/error state craftsmanship.

**Proposal:**
- Design token pass: spacing, elevation, type scale, semantic colors.
- Add global command palette (`⌘K` / `Ctrl+K`).
- Add microcopy system for empty/error states.

---

## Accessibility + demographic inclusivity gaps

To be genuinely practical across demographics, prioritize:

1. **WCAG baseline:** contrast, focus states, reduced motion, semantic landmarks.
2. **Cognitive load controls:** “simple language mode”, optional jargon explanations.
3. **Progressive disclosure:** hide advanced controls by default; expand on demand.
4. **Auditability by non-engineers:** natural-language summaries of why the system took an action.

---

## Why current multi-agent behavior still feels “single-threaded”

Even with subagents, control appears centralized around moderator-triggered delegation. This often looks like:
- Main agent plans.
- Delegates one or a few tasks.
- Awaits outputs.
- Reconciles serially.

That is **parallel execution**, but not yet a **self-organizing swarm**.

### Core missing properties for swarm-like behavior

1. **Shared blackboard with explicit hypotheses**
   Agents should continuously post/attack/refine hypotheses, not only deliver finalized subtasks.

2. **Dynamic role reassignment**
   Agent roles should mutate based on current uncertainty and observed performance, not fixed persona assignment.

3. **Asynchronous cross-agent critique loops**
   Agents should critique each other’s partial outputs mid-flight, not only at the end.

4. **Budget-aware democratic governance**
   Votes should be weighted by expertise, recency, and calibration — not only equal-turn voting.

5. **Conflict-preserving consensus**
   Keep minority reports when uncertainty remains high; don’t force premature convergence.

---

## Concrete evolution path: from parallelism → emergent collaboration

### Phase A: Blackboard-first collaboration protocol
- Introduce structured artifacts: `Claim`, `Evidence`, `Counterexample`, `Decision`, `OpenQuestion`.
- Every agent can append/update artifacts; moderator curates, not authors everything.
- UI should show evolving claim graph, not just chat/event stream.

### Phase B: Democratic process upgrades
- Weighted voting = f(domain_score, historical calibration, freshness, confidence).
- Add quorum + veto logic for risky actions.
- Add abstain + request-more-evidence vote types.

### Phase C: Market-like task allocation
- Agents bid for tasks with confidence/cost estimates.
- Scheduler assigns based on expected information gain per token/latency budget.
- Re-auction when blockers emerge.

### Phase D: Temporal windows on log streams (your EnLIghTenED core)
- Define explicit sliding temporal windows with different scopes:
  - micro (recent tactical events)
  - meso (current objective history)
  - macro (session-wide behavior signatures)
- Use windows to modulate prompts, voting weight, and anomaly detection.

---

## SWE-bench-verified benchmarking strategy suggestions

### 1) Define baseline families clearly
- Single-agent baseline (same model/tools, no delegation).
- Parallel-only baseline (today’s behavior).
- Enhanced democratic swarm variants (A/B/C above).

### 2) Add process metrics (not just pass@k)
Track:
- tool-call efficiency
- time-to-first-correct-patch
- patch churn / reversions
- cross-agent disagreement rate
- disagreement resolution latency
- calibration error of votes vs correctness

### 3) Ablation table for paper credibility
- Remove temporal windows.
- Remove weighted voting.
- Remove critique loops.
- Remove dynamic role reassignment.

Then quantify delta on both quality and cost.

---

## Product framing recommendations

To avoid “direct clone” positioning against other agent frameworks:

1. Lead with **governance + observability** as product identity.
2. Position as **democratic orchestration runtime** for high-assurance code tasks.
3. Highlight **human-legible reasoning trails** and postmortem-grade logs.

---

## 30-day execution plan (high-impact)

1. Ship role-based UI presets and command palette.
2. Introduce structured blackboard artifact model in backend events.
3. Add weighted vote schema + confidence calibration logging.
4. Build benchmark harness with reproducible configs and ablations.
5. Publish one internal report comparing single vs parallel vs democratic swarm.

---

## Final recommendation

You are **close to a differentiator**: the pieces for secure execution, event streaming, and delegation already exist. The next leap is less about adding more agents and more about changing the **interaction protocol** among agents and making that protocol **visible, trustworthy, and ergonomic** for different user personas.
