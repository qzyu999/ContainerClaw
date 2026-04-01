# RCA Part 2: DAG Rendering Issues After Backbone Fix

## TL;DR

The **chatroom data is now correct** — backbone causality is perfect. The problems are in three other layers:

1. **P0**: `dag_edges` Flink table is **still empty** (0 rows) — the Flink job was not restarted or `dag_edges` was not dropped/recreated
2. **P1**: The **bridge's `_lookup_dag_edges`** reads from the chatroom log (bypassing `dag_edges`), but its edge format has the `ts` field named `updated_at` while the UI expects `ts` — causing broken timestamps
3. **P2**: The **DagView.tsx layout engine** has a fundamentally broken tiering algorithm that misclassifies linear chains as branching trees, producing phantom "Tier 1..4" lanes
4. **P3**: Node labels display **raw UUIDs** instead of actor names due to the bridge returning `child_label` which is the `actor_id`, but the layout engine uses `child_label` ORing with `extractLabel(e.child)` where `e.child` is a raw UUID

---

## Verified: Chatroom Backbone is Correct ✅

Tracing the `parent_event_id` chains from the new logs:

```
Human       (2f9efe6e)  → parent: ""       (ROOT)
Boot        (79a9b974)  → parent: ""       (ROOT, edge_type=ROOT)
ElecStart1  (f73f0456)  → parent: 2f9efe6e (Human) ← ✅ FIXED from pt1!
  Round1    (51553f00)  → parent: f73f0456  (ElecStart1) ← ✅ fan-out
  Tally1    (95cb4ee1)  → parent: f73f0456  (ElecStart1) ← ✅ fan-out
  Summary1  (1adcd8ee)  → parent: f73f0456  (ElecStart1) ← ✅ fan-out
  Winner1   (8b71fb60)  → parent: f73f0456  (ElecStart1) ← ✅ fan-out
Alice       (4b8f2592)  → parent: 8b71fb60  (Winner) ← ✅ backbone
Checkpoint1 (3e918786)  → parent: 4b8f2592  (Alice) ← ✅ backbone
ElecStart2  (9e1d11e7)  → parent: 3e918786  (CP1) ← ✅ backbone
  Round2    (4b08087a)  → parent: 9e1d11e7  (ElecStart2) ← ✅ fan-out
  Tally2    (f389bdda)  → parent: 9e1d11e7  (ElecStart2) ← ✅ fan-out
  Summary2  (21e46cbd)  → parent: 9e1d11e7  (ElecStart2) ← ✅ fan-out
Finish      (1c20cdea)  → parent: 3e918786  (CP1) ← ✅ backbone
Checkpoint2 (5969b81e)  → parent: 1c20cdea  (Finish) ← ✅ backbone
```

The backbone is: `Human → ElecStart1 → Winner → Alice → CP1 → Finish → CP2`
With fan-outs from ElecStart1 and ElecStart2.

**This is exactly the intended structure.** The data layer is working.

---

## Issue P0: `dag_edges` Table Still Empty

```
📊 TABLE: containerclaw.dag_edges
   ℹ️  Looking up 15 known event IDs...
   ✅ Found 0 matching rows.
```

Despite fixing the column name in `DagPipeline.java`, the table is still empty. This means either:
1. The Flink job container was not rebuilt/restarted after the Java change
2. The old `dag_edges` table (with stale schema) was not dropped before restart
3. The Flink job crashed and is not running

### Fix
```bash
# Must do all three:
docker-compose restart claw-telemetry   # 1. Restart Flink job
# Or better: stop, drop table, restart
```

> [!IMPORTANT]
> This is an ops issue, not a code issue. The column fix IS correct in the source. The table just needs a fresh restart.

However, **P0 is actually not the user's immediate pain point** — the bridge's `_lookup_dag_edges` bypasses the Flink table and reads directly from the chatroom log. So the DAG *should* still render from the log scan. The rendering problems are in the bridge output format and the UI layout engine.

---

## Issue P1: Bridge `_lookup_dag_edges` Returns Wrong Field Names

The bridge scans the chatroom log directly and constructs edge dicts (bridge.py:356-363):

```python
edges.append({
    "parent": parent_eid if parent_eid else "ROOT",
    "child": eid_arr[i].as_py(),
    "child_label": actor_arr[i].as_py(),
    "edge_type": edge_type if edge_type else "SEQUENTIAL",
    "status": status,
    "updated_at": ts_arr[i].as_py(),   # ← field is "updated_at"
})
```

The UI's `DagEdge` TypeScript interface (api.ts:163-171):

```typescript
export interface DagEdge {
  parent: string;
  child: string;
  parent_label?: string;
  child_label?: string;
  status: 'ACTIVE' | 'THINKING' | 'DONE';
  updated_at: number;
  ts?: number;          // ← optional, may be used by layout engine
}
```

In DagView.tsx:96-97, the layout engine reads timestamps:
```typescript
if (!nodeTimestamps.has(e.child)) nodeTimestamps.set(e.child, Number(e.ts));
if (!nodeTimestamps.has(e.parent)) nodeTimestamps.set(e.parent, Number(e.ts) - 500);
```

**Bug:** It reads `e.ts` which is `undefined` — the bridge returns `updated_at`, not `ts`. So `Number(undefined) = NaN` → every node gets `NaN` timestamps → chronological sorting breaks → random node ordering → chaotic layout.

### Fix
Either:
- **Option A (Bridge):** Add `"ts": ts_arr[i].as_py()` to the bridge output alongside `updated_at`
- **Option B (UI):** Read `e.updated_at || e.ts` in DagView.tsx

**Recommendation: Option A** — add both fields to the bridge output for compatibility.

---

## Issue P2: Layout Engine Tiering Algorithm is Broken

The DagView.tsx tier assignment (lines 106-128) uses a BFS algorithm:

```typescript
const children = childrenOf.get(id) || [];
if (children.length === 1) {
    visitQueue.push({ id: children[0], tier });         // Stay in same tier
} else if (children.length > 1) {
    const mainChild = children.find(c => 
        (nodeLabels.get(c) || '').toLowerCase().includes('moderator')
    ) || children[0];
    visitQueue.push({ id: mainChild, tier });           // First/moderator stays
    children.filter(c => c !== mainChild).forEach(sub => {
        visitQueue.push({ id: sub, tier: tier + 1 });   // Others go to next tier
    });
}
```

**Problem:** When a node has multiple children, the algorithm picks ONE child to keep in the current tier (preferring "moderator" label) and pushes ALL others to the next tier. But look at what the chatroom edges produce:

```
ROOT has 2 children:  Human(2f9efe6e), Boot(79a9b974)     → Human stays, Boot → Tier 1
ElecStart1 has 4 children: Round1, Tally1, Summary1, Winner1
  → Round1 (or whichever has "moderator" label) stays, others → Tier 2
Checkpoint1 has 2 children: ElecStart2, Finish
  → one stays, other → Tier 3
ElecStart2 has 3 children: Round2, Tally2, Summary2
  → one stays, others → Tier 4
```

**This is why the user sees "Central Timeline, Tier 1, Tier 2, Tier 3, Tier 4"** — the algorithm was designed for a tree where each branch is a different agent. But the actual data has **fan-outs from election events** where ALL children are "Moderator" — so the `includes('moderator')` heuristic doesn't help distinguish them, and the first child wins arbitrarily.

### Expected Behavior

The elections should be **collapsible groups**, not separate tiers. The correct layout would be:

**Central Timeline (backbone):**
```
Human → Boot → ElecStart1 → Winner1 → Alice → CP1 → ElecStart2 → Finish → CP2
```

**Tier 1 (election details — collapsible):**
```
From ElecStart1: Round1, Tally1, Summary1
From ElecStart2: Round2, Tally2, Summary2
```

### Fix

The tiering algorithm needs to use `edge_type` to decide what goes to the backbone vs what branches off. The `edge_type` field is already in the data but is **NOT passed through by the bridge** — the bridge returns `edge_type` but the UI's `DagEdge` interface doesn't include it!

**Changes needed:**
1. **api.ts**: Add `edge_type?: string` to `DagEdge` interface
2. **DagView.tsx**: Use `edge_type` to determine tier assignment:
   - If a child has `edge_type === "SEQUENTIAL"` AND shares the same parent as the backbone, it stays in the central timeline
   - Election detail events (Round, Tally, Summary) that fan out from the same parent get pushed to Tier 1
   - The backbone should be determined by following the **longest SEQUENTIAL chain** from ROOT, not by label heuristics

### Proposed Algorithm

```typescript
// Build a "backbone set" by following the longest SEQUENTIAL chain
const backboneSet = new Set<string>();
let cursor = roots[0]; // Start from ROOT
while (cursor) {
    backboneSet.add(cursor);
    const children = childrenOf.get(cursor) || [];
    // Find the SEQUENTIAL child that advances the backbone
    const nextBackbone = children.find(c => 
        edges.find(e => e.parent === cursor && e.child === c)?.edge_type === 'SEQUENTIAL'
        && children.length === 1  // If only child, it's backbone
    ) || children.find(c =>
        // For fan-outs, pick the child whose OWN children are SEQUENTIAL
        // i.e., the one that continues the chain, not a leaf detail
        (childrenOf.get(c) || []).length > 0
    );
    if (!nextBackbone) break;
    cursor = nextBackbone;
}

// Tier assignment: backbone = 0, everything else = 1
```

---

## Issue P3: Node Labels Show UUIDs

The user sees "9e1d11e7-..." instead of "Alice" because:

1. The bridge returns `child_label` as the `actor_id` (e.g., "Moderator", "Alice")
2. The parent node doesn't get a label because `parent_label` is not in the bridge output
3. DagView.tsx line 92: `nodeLabels.set(e.parent, e.parent_label || extractLabel(e.parent))`
4. `e.parent_label` is `undefined` (bridge doesn't send it)
5. `extractLabel(e.parent)` receives a raw UUID like `"2f9efe6e-0929-..."` and returns it as-is (no `|` pipe character found)

So ROOT and parent nodes display as raw UUIDs.

**Additionally:** Most events have `actor_id = "Moderator"`, so even the child labels are repetitive. The user probably expects to see "Starting Election", "Winner: Alice", etc. — the content-based label, not the actor.

### Fix

Two changes:

1. **Bridge**: Include content-derived labels and `parent_label`. For each edge, derive a short label from the event content or type:

```python
# Derive a meaningful label from event content
def _derive_label(actor_id, content, event_type):
    if event_type == "checkpoint":
        return "Checkpoint"
    if event_type == "finish":
        return "Task Complete"
    if "Starting Election" in content:
        return "Election"
    if "Winner" in content:
        return content[:30]  # "🏆 Winner: Alice"
    return actor_id
```

2. **Build a parent→label lookup** by scanning all events first, then populating `parent_label` for each edge from the same scan.

---

## Issue P4 (Cosmetic): All Non-Terminal Events Show "ACTIVE"

The bridge status mapping (bridge.py:349-354):
```python
if event_type in ("finish", "done", "checkpoint"):
    status = "DONE"
elif event_type == "action":
    status = "THINKING"
else:
    status = "ACTIVE"    # ← everything else gets ACTIVE
```

Most events are type `"thought"` or `"output"` or `"voting"` — all get `ACTIVE` status with a pulsing green glow. The user sees a DAG full of flashy green dots that never settle.

### Fix

```python
if event_type in ("finish", "done", "checkpoint"):
    status = "DONE"
elif event_type in ("action", "voting"):
    status = "THINKING"
elif event_type == "thought":
    status = "DONE"      # Thoughts are past events, not actively computing
else:
    status = "ACTIVE"    # Only truly active if output/streaming
```

Or better: check if the event has a more recent successor in the backbone (parent → child exists). If so, the event is DONE.

---

## Root Cause Summary

| # | Issue | Layer | Impact | Fix Complexity |
|---|-------|-------|--------|----------------|
| P0 | `dag_edges` table empty | Ops (Flink restart) | Inspection script can't verify; bridge bypasses anyway | Restart container |
| P1 | `ts` field undefined → NaN timestamps → broken sort | Bridge → UI contract | **Nodes appear in random order** | 1 line in bridge.py |
| P2 | Tiering algorithm treats linear chains as branches | UI layout engine | **Creates phantom Tier 1-4 lanes** | Rewrite tiering logic (~30 lines) |
| P3 | Parent labels are raw UUIDs; child labels are all "Moderator" | Bridge output | **Nodes display as UUIDs** instead of meaningful names | Add `parent_label` + content-derived labels in bridge |
| P4 | All non-terminal events show ACTIVE (pulsing green) | Bridge status mapping | **Everything glows as if actively computing** | Fix status classification |

---

## Recommended Fix Order

1. **P1** (1 line) — Fix `ts` field in bridge output → timestamps stop being NaN → correct node ordering
2. **P3** (10 lines) — Add parent_label and content-derived labels → nodes show "Human", "Election", "Alice" instead of UUIDs
3. **P4** (3 lines) — Fix status classification → nodes show correct DONE/ACTIVE states
4. **P2** (30 lines) — Rewrite tiering algorithm to use edge_type → correct Central Timeline vs Tier 1 layout
5. **P0** (ops) — Restart Flink container with fresh `dag_edges` table

All of P1-P4 are in `bridge.py` and `DagView.tsx` — no changes needed to the agent code (the backbone is correct).
