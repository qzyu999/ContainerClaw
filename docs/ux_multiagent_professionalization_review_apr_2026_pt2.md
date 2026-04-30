# UX + Multi-Agent Professionalization Review (April 30, 2026)

## Why this review exists
This note is a practical PTAL for pushing ContainerClaw toward:
1. **Apple-level clarity/fit-and-finish** for mixed user demographics.
2. **Truer multi-agent swarm behavior** beyond one-agent-at-a-time + opportunistic parallelism.
3. **Bench-ready scientific framing** for SWE-bench-verified + EnLIghTenED paper claims.
4. **A real MVP path** from today's single-threaded moderator loop to a policy-centric democratic forum on Fluss.

---

## Current strengths worth preserving

- Strong architectural separation of concerns (agent, gateway, bridge, telemetry, UI) with a security-first framing in project docs.
- Existing UI already exposes rich surfaces (chatroom, DAG, metrics, anchor, snorkel, explorer, board), which is a strong foundation.
- Subagent lifecycle infrastructure already exists (`SubagentManager`) with spawn/cancel/status semantics and event chaining hooks.
- You already have the right intuition: practical “agent civilization” requires **local autonomy + shared signal mesh**.

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

## Federal checks-and-balances model for agents (policy-centric)

Treat your system like a small constitutional government:

- **Legislative layer (Policy Council):** proposes/updates policies and risk budgets.
- **Executive layer (Worker Swarm):** executes tasks using delegated authority.
- **Judicial layer (Audit/Critic agents):** challenges actions against policy + evidence.

### Practical policy objects (MVP-ready)
Each policy is a versioned object in Fluss:
- `policy_id`, `version`, `scope`, `rule_text`, `risk_level`, `enforcement_mode`.
- Enforcement modes: `advisory`, `soft_block`, `hard_block`.

### Decision types
- `ROUTINE_EXEC` (simple majority)
- `RISKY_EXEC` (quorum + critic signoff)
- `POLICY_CHANGE` (supermajority + cooling window)

This creates a programmable governance kernel without needing full decentralization on day 1.

---

## Fluss-native “central mesh + local autonomy” architecture

Your Waymo analogy is exactly right. The usable design is:

1. **Local loop (edge autonomy):** each agent can think/tool/act independently within local constraints.
2. **Global mesh (Fluss streamhouse):** agents consume consensus-relevant deltas (policy updates, conflicts, high-priority evidence, blocked actions).
3. **Selective synchronization:** agents sync only relevant partitions/windows, not the whole universe.

### Suggested stream topics/tables
- `agent.events.raw`
- `blackboard.claims`
- `blackboard.evidence`
- `governance.votes`
- `governance.decisions`
- `policy.ledger`
- `agent.reputation`
- `conflict.queue`

### Temporal windows (EnLIghTenED fit)
- **Micro window:** recent tool attempts + immediate blockers.
- **Meso window:** active objective and current branch of reasoning.
- **Macro window:** long-horizon policy, reputation, and prior failures.

Agents should receive different prompt/context slices per window, rather than one blended history.

---

## MVP path: decentralized sync through project blackboard

## MVP-0 (1-2 weeks): “Shared artifacts first”
- Convert Project Board from UI-only task view into canonical backend artifact stream.
- Add artifact schema: `Claim`, `Evidence`, `Counterexample`, `OpenQuestion`, `Decision`.
- Require every subagent completion to append at least one artifact.

**Success metric:** >80% of successful tasks include traceable evidence-linked decisions.

## MVP-1 (2-4 weeks): “Forum voting, not just delegation”
- Add vote event type with fields: `proposal_id`, `vote`, `confidence`, `rationale_ref`.
- Add decision rules by action risk class.
- Add critic agents that can issue `challenge` events before execution.

**Success metric:** measurable drop in unsafe/tool-waste actions at equal pass rate.

## MVP-2 (4-6 weeks): “Reputation + weighted democracy”
- Maintain per-agent calibration and domain score in `agent.reputation`.
- Weighted voting with cap/floor to prevent oligarchic lock-in.
- Auto-trigger minority report when vote entropy is high.

**Success metric:** better cost-adjusted success vs unweighted majority.

## MVP-3 (6-8 weeks): “Mesh synchronization controls”
- Partition streams by project/objective/risk domain.
- Agents subscribe dynamically based on current subgoal.
- Add stale-context detector (forces refresh when confidence decays).

**Success metric:** lower token overhead per resolved issue while preserving quality.

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
- Democratic forum (unweighted votes).
- Democratic forum + reputation weighting.
- Democratic forum + temporal windows.

### 2) Add process metrics (not just pass@k)
Track:
- tool-call efficiency
- time-to-first-correct-patch
- patch churn / reversions
- cross-agent disagreement rate
- disagreement resolution latency
- calibration error of votes vs correctness
- policy-violation interception rate

### 3) Ablation table for paper credibility
- Remove temporal windows.
- Remove weighted voting.
- Remove critique loops.
- Remove dynamic role reassignment.
- Remove policy challenge layer.

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
2. Make Project Board artifacts canonical in backend streams.
3. Add governance vote + challenge events and a basic policy ledger.
4. Build benchmark harness with reproducible configs and ablations.
5. Publish one internal report comparing single vs parallel vs democratic forum.

---

## Final recommendation

You are close to a differentiator: the pieces for secure execution, event streaming, and delegation already exist. The next leap is less about adding more agents and more about changing the **interaction constitution** among agents — then making that constitution visible, measurable, and enforceable through Fluss.
