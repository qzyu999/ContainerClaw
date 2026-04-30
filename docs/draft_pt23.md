# Board Work-Item Threads: Structured Progress Documentation

> **Scope:** Extend the `ProjectBoard` from a flat task tracker (title + description + status) into a work-item documentation system where agents post structured progress entries — analysis, findings, conclusions, blockers — directly on board items. Inspired by Azure DevOps "Discussion" threads on work items, but constrained by the reality that unchecked agent verbosity produces sprawl with net negative value.

---

## 1. The Problem: Board Items Are Write-Once, Reason-Never

### 1.1 What Exists Today

The current `BoardItem` ([agent.proto:118-126](file:///.../containerclaw/proto/agent.proto#L118-L126)) has exactly six fields:

```protobuf
message BoardItem {
  string id = 1;
  string type = 2;       // epic | story | task
  string title = 3;
  string description = 4;
  string status = 5;     // todo | in_progress | done
  string assigned_to = 6;
  double created_at = 7;
}
```

The `BoardTool` ([tools.py:356-470](file:///.../containerclaw/agent/src/tools.py#L356-L470)) exposes four actions: `create`, `update` (status only), `delete`, `list`. The `update` action can only change `status` — there is no way to append notes, record findings, or document why a status changed.

### 1.2 The Consequence

When an agent marks `T-003` as `done`, the board shows:

```
✅ [T-003] Fix import ordering in utils.py → Carol
```

But there is zero record of:
- What Carol investigated
- What the root cause was
- What tests were run
- Whether the fix has side effects
- Why the approach was chosen over alternatives

The board is a **to-do list**, not a **knowledge artifact**. The election protocol invests 5× LLM calls per turn to select the best agent — then the selected agent's reasoning evaporates the moment it publishes its output to the chatroom stream. The chatroom captures *everything*, but finding "what did Carol conclude about T-003?" requires scrolling through hundreds of messages across multiple election cycles.

### 1.3 The Azure DevOps Analogy

In Azure DevOps, every work item has a **Discussion** tab: a threaded timeline of comments, status transitions, linked commits, and test results. Engineers don't just flip a status — they document *why*, creating an audit trail that future engineers (or future agents) can reference without re-deriving the reasoning.

ContainerClaw's board items need the same capability, but with guardrails against agent verbosity.

---

## 2. The Sprawl Problem: Why This Can Go Wrong

> _"I am worried that it ends up being some sprawling set of tasks with net negative benefit — something I saw on a long-running job where there were just many many tasks piled up without certainty that they were directing the agents in a positive direction."_

This is the central design constraint. Unrestricted agent commentary produces:

1. **Comment Sprawl:** Every election cycle, every agent posts an "update." After 20 cycles, each board item has 100+ comments, most redundant.
2. **Task Sprawl:** Agents create sub-tasks for every minor observation. A 3-task board becomes 47 tasks with unclear dependencies.
3. **Information Entropy:** The board becomes *less* useful over time — the signal-to-noise ratio drops below 1.0, and both agents and humans stop consulting it.

### 2.1 The Design Principle: Bounded Documentation

The system must enforce:

| Constraint | Config Key | Default | Rationale |
|:---|:---|:---|:---|
| **Comment budget per item** | `board_comments.max_comments_per_item` | 10 | Prevents infinite accumulation |
| **Comment size cap** | `board_comments.comment_max_chars` | 500 | Forces concise, actionable entries |
| **Staleness pruning** | `board_comments.stale_threshold_cycles` | 10 | Auto-flags items with no activity |
| **Summarization gate** | `board_comments.summarize_count` | 5 | Compresses oldest N comments when budget hit |
| **Task creation throttle** | `board_comments.max_items_per_cycle` | 3 | Prevents task explosion |
| **Mandatory rationale** | (enforced in tool logic) | — | Makes status changes auditable |

All tunables live in `config.yaml` → `agents.settings.board_comments` ([config.yaml:143-150](file:///Users/jaredyu/Desktop/open_source/containerclaw/config.yaml#L143-L150)).

These constraints are **not optional**. They are the architectural immune system against sprawl.

---

## 3. Data Model Changes

### 3.1 New: Board Comment Events (Fluss Table)

A new Fluss append-only log table for work-item comments:

```python
# schemas.py — NEW
BOARD_COMMENT_EVENTS_SCHEMA = pa.schema([
    pa.field("session_id", pa.string()),
    pa.field("ts", pa.int64()),
    pa.field("item_id", pa.string()),          # FK → BoardItem.id (e.g. "T-003")
    pa.field("comment_id", pa.string()),       # UUID
    pa.field("action", pa.string()),           # "add" | "archive" | "summarize"
    pa.field("author", pa.string()),           # agent_id or "System"
    pa.field("category", pa.string()),         # "analysis" | "finding" | "conclusion" | "blocker" | "status_change" | "summary"
    pa.field("content", pa.string()),          # The comment body (≤500 chars)
    pa.field("archived", pa.bool_()),          # Soft-delete flag
])
BOARD_COMMENT_EVENTS_TABLE = "board_comment_events"
```

### 3.2 Extended: Board Events Schema

Add a `reason` field to the existing `BOARD_EVENTS_SCHEMA` to capture status transition rationale:

```python
# schemas.py — MODIFIED (add field to existing schema)
BOARD_EVENTS_SCHEMA = pa.schema([
    # ... existing fields ...
    pa.field("reason", pa.string()),           # NEW: Why the status changed
])
```

### 3.3 Extended: Protobuf Messages

```protobuf
// agent.proto — NEW messages

message BoardComment {
  string comment_id = 1;
  string item_id = 2;
  string author = 3;
  string category = 4;    // "analysis" | "finding" | "conclusion" | "blocker" | "status_change" | "summary"
  string content = 5;
  int64 ts = 6;
  bool archived = 7;
}

// MODIFIED: extend existing BoardItem
message BoardItem {
  string id = 1;
  string type = 2;
  string title = 3;
  string description = 4;
  string status = 5;
  string assigned_to = 6;
  double created_at = 7;
  repeated BoardComment comments = 8;       // NEW: latest non-archived comments
  string last_reason = 9;                   // NEW: reason for most recent status change
}

// NEW RPC
service AgentService {
  // ... existing RPCs ...
  rpc GetBoardItem (BoardItemRequest) returns (BoardItemDetail);
}

message BoardItemRequest {
  string session_id = 1;
  string item_id = 2;
}

message BoardItemDetail {
  BoardItem item = 1;
  repeated BoardComment comments = 2;       // Full comment thread
}
```

---

## 4. Tool Interface Changes

### 4.1 Extended `BoardTool` Actions

The `BoardTool` gains two new actions and one modified action:

```python
# tools.py — MODIFIED BoardTool.get_schema()
{
    "action": {
        "type": "string",
        "description": "Board action: 'create', 'update', 'delete', 'list', 'comment', or 'view'.",
        "enum": ["create", "update", "delete", "list", "comment", "view"],
    },
    # ... existing params ...

    # NEW params for 'comment' action:
    "category": {
        "type": "string",
        "description": "Comment category: 'analysis', 'finding', 'conclusion', or 'blocker'.",
        "enum": ["analysis", "finding", "conclusion", "blocker"],
    },
    "content": {
        "type": "string",
        "description": f"Comment body (max {config.BOARD_COMMENTS.comment_max_chars} chars). Be concise and actionable.",
    },

    # MODIFIED: 'update' now accepts optional 'reason'
    "reason": {
        "type": "string",
        "description": "Why this status change is being made (required for update).",
    },
}
```

#### Action: `comment`

Posts a structured comment on an existing board item.

```python
# tools.py — BoardTool.execute() (new action)
if action == "comment":
    item_id = params.get("item_id", "")
    category = params.get("category", "analysis")
    content = params.get("content", "")

    if not item_id or not content:
        return ToolResult(success=False, output="", error="'comment' requires 'item_id' and 'content'.")

    max_chars = config.BOARD_COMMENTS.comment_max_chars
    if len(content) > max_chars:
        content = content[:max_chars]  # Hard truncate — enforce discipline

    result = await self.board.add_comment(
        item_id=item_id,
        author=agent_id,
        category=category,
        content=content,
    )
    if result:
        return ToolResult(success=True, output=f"Comment added to {item_id} [{category}].")
    return ToolResult(success=False, output="", error=f"Item {item_id} not found or comment budget exceeded.")
```

#### Action: `view`

Returns the full detail of a single board item including its comment thread.

```python
if action == "view":
    item_id = params.get("item_id", "")
    if not item_id:
        return ToolResult(success=False, output="", error="'view' requires 'item_id'.")
    detail = self.board.get_item_detail(item_id)
    if detail:
        return ToolResult(success=True, output=detail)
    return ToolResult(success=False, output="", error=f"Item {item_id} not found.")
```

#### Modified: `update` with mandatory `reason`

```python
if action == "update":
    item_id = params.get("item_id", "")
    status = params.get("status", "")
    reason = params.get("reason", "")

    if not reason:
        return ToolResult(
            success=False, output="",
            error="'update' requires a 'reason' explaining why the status is changing.",
        )

    item = await self.board.update_status(item_id, status, actor=agent_id, reason=reason)
    # ... auto-posts a "status_change" category comment with the reason
```

### 4.2 Board Summary Enhancement

The `list` action now shows comment counts and last update:

```
⬜ [T-001] Investigate flaky test in auth module → David
   💬 3 comments · Last: "Root cause: race condition in session cleanup" (2 min ago)
🟡 [T-002] Refactor import ordering → Carol
   💬 1 comment · Last: "Analyzing circular dependency in utils.py" (5 min ago)
✅ [T-003] Fix null pointer in parser → Carol
   💬 2 comments · Last: "Verified: all 47 tests pass" (done)
```

---

## 5. Anti-Sprawl Mechanisms (Implementation)

### 5.1 Comment Budget Enforcement

```python
# tools.py — ProjectBoard.add_comment()
# All limits sourced from config.yaml → agents.settings.board_comments

async def add_comment(self, item_id, author, category, content):
    # Count active (non-archived) comments for this item
    active_comments = [c for c in self.comments.get(item_id, []) if not c["archived"]]

    if len(active_comments) >= config.BOARD_COMMENTS.max_comments_per_item:
        # Auto-summarize: compress oldest N comments into a single "summary" entry
        await self._summarize_oldest(item_id, count=config.BOARD_COMMENTS.summarize_count)

    comment = {
        "comment_id": str(uuid.uuid4()),
        "item_id": item_id,
        "author": author,
        "category": category,
        "content": content[:config.BOARD_COMMENTS.comment_max_chars],
        "ts": int(time.time() * 1000),
        "archived": False,
    }
    self.comments.setdefault(item_id, []).append(comment)
    await self._publish_comment_event("add", comment)
    return comment
```

### 5.2 Auto-Summarization

When the comment budget is hit, the system compresses the oldest half into a single summary:

```python
async def _summarize_oldest(self, item_id, count=None):
    """Compress the oldest N comments into a summary comment."""
    count = count or config.BOARD_COMMENTS.summarize_count
    trunc = config.BOARD_COMMENTS.summary_line_truncate
    max_chars = config.BOARD_COMMENTS.comment_max_chars

    active = [c for c in self.comments[item_id] if not c["archived"]]
    to_summarize = active[:count]

    # Build summary from the compressed comments
    summary_lines = []
    for c in to_summarize:
        summary_lines.append(f"[{c['category']}] {c['author']}: {c['content'][:trunc]}...")

    summary_content = "📦 Archived summary:\n" + "\n".join(summary_lines)

    # Archive the originals
    for c in to_summarize:
        c["archived"] = True
        await self._publish_comment_event("archive", c)

    # Create the summary comment
    summary = {
        "comment_id": str(uuid.uuid4()),
        "item_id": item_id,
        "author": "System",
        "category": "summary",
        "content": summary_content[:max_chars],
        "ts": int(time.time() * 1000),
        "archived": False,
    }
    self.comments[item_id].append(summary)
    await self._publish_comment_event("summarize", summary)
```

### 5.3 Task Creation Throttle

```python
# tools.py — ProjectBoard.create_item() modification
# Limits from config.yaml → agents.settings.board_comments

async def create_item(self, ...):
    # Check how many items were created in the current reconciler cycle
    window = config.BOARD_COMMENTS.item_creation_window_s
    recent_creates = sum(
        1 for item in self.items
        if item["created_at"] > (time.time() - window)
    )
    if recent_creates >= config.BOARD_COMMENTS.max_items_per_cycle:
        return None  # Throttled — caller receives error message
    # ... existing creation logic
```

### 5.4 Staleness Detection

During each reconciler cycle checkpoint, stale items are flagged:

```python
# tools.py — ProjectBoard.prune_stale()
# Limits from config.yaml → agents.settings.board_comments

def prune_stale(self):
    """Mark items as stale if they haven't been updated in N cycles."""
    threshold = config.BOARD_COMMENTS.stale_threshold_cycles
    cycle_dur = config.BOARD_COMMENTS.stale_cycle_duration_s
    now = time.time()
    for item in self.items:
        if item["status"] == "in_progress":
            comments = self.comments.get(item["id"], [])
            last_activity = max(
                [c["ts"] / 1000 for c in comments if not c["archived"]],
                default=item["created_at"]
            )
            if now - last_activity > threshold * cycle_dur:
                # Auto-post a staleness warning
                # Visible to agents in the board summary
                item["_stale"] = True
```

---

## 6. UI Changes

### 6.1 Board Card Expansion

The `ProjectBoard.tsx` component gains an expandable card view:

```
┌──────────────────────────────────────────────┐
│ ⬜ T-001  Investigate flaky auth test        │
│ → David                                      │
│                                              │
│ ▼ 3 comments                                 │
│ ┌──────────────────────────────────────────┐ │
│ │ 🔍 [analysis] David · 5 min ago          │ │
│ │ "The test fails intermittently because   │ │
│ │  session cleanup runs in a background    │ │
│ │  thread that races with the next test    │ │
│ │  setup."                                 │ │
│ ├──────────────────────────────────────────┤ │
│ │ 💡 [finding] David · 3 min ago           │ │
│ │ "The root cause is in conftest.py:42.    │ │
│ │  The `yield` fixture doesn't await the  │ │
│ │  cleanup coroutine."                     │ │
│ ├──────────────────────────────────────────┤ │
│ │ 📋 [conclusion] David · 1 min ago        │ │
│ │ "Fix: replace `yield` with              │ │
│ │  `yield await cleanup()`. All 12 tests  │ │
│ │  pass on 5 consecutive runs."            │ │
│ └──────────────────────────────────────────┘ │
└──────────────────────────────────────────────┘
```

Category icons:
- 🔍 `analysis` — investigation in progress
- 💡 `finding` — discovered fact or root cause
- ✅ `conclusion` — final determination
- 🚧 `blocker` — impediment requiring attention
- 🔄 `status_change` — auto-generated on status transitions
- 📦 `summary` — auto-generated from archived comments

### 6.2 New API Endpoint: Board Item Detail

```python
# bridge.py — NEW endpoint
@app.route("/board/<session_id>/item/<item_id>")
def get_board_item_detail(session_id, item_id):
    """Fetch a single board item with its full comment thread."""
    response = stub.GetBoardItem(agent_pb2.BoardItemRequest(
        session_id=session_id,
        item_id=item_id,
    ))
    return jsonify({
        "status": "ok",
        "item": { ... },
        "comments": [ ... ],
    })
```

---

## 7. Agent Prompt Guidance

The board tool description must guide agents toward quality over quantity. The tool description becomes:

```python
BoardTool.description = (
    "Interact with the shared project board. Use this to track work items "
    "and document your reasoning as structured comments.\n\n"
    "GUIDELINES:\n"
    "- Use 'comment' to document significant findings, not routine updates.\n"
    "- Each comment must add NEW information. Do not repeat what's already there.\n"
    "- Use categories: 'analysis' (investigating), 'finding' (discovered fact), "
    "'conclusion' (final determination), 'blocker' (need help).\n"
    "- When updating status, always provide a 'reason'.\n"
    "- Before creating a new item, check if one already covers your intent.\n"
    f"- The board has a comment budget (max {config.BOARD_COMMENTS.max_comments_per_item} per item). Be concise.\n\n"
    "Actions: 'create', 'update' (item_id, status, reason), "
    "'delete' (item_id), 'list', 'comment' (item_id, category, content), "
    "'view' (item_id — shows full comment thread)."
)
```

---

## 8. Implementation Phases

### Phase 1: Data Layer (Effort: 2-3 hours)

| Task | Details |
|:---|:---|
| Add `BOARD_COMMENT_EVENTS_SCHEMA` to `schemas.py` | New Fluss table for comment events |
| Add `reason` field to `BOARD_EVENTS_SCHEMA` | Status change rationale |
| Extend `ProjectBoard` with `comments` dict and `add_comment` / `get_item_detail` methods | In-memory state rebuilt from Fluss replay |
| Wire `comment_events` table in `fluss_client.py` | Table creation + writer init |

### Phase 2: Tool Layer (Effort: 2-3 hours)

| Task | Details |
|:---|:---|
| Add `comment` and `view` actions to `BoardTool` | New action handlers |
| Modify `update` action to require `reason` | Mandatory rationale |
| Implement comment budget enforcement (max 10) | With auto-summarization |
| Implement task creation throttle (max 3/cycle) | Time-windowed rate limit |
| Update `get_board_summary()` to include comment counts | Enhanced list output |

### Phase 3: Protobuf + Bridge (Effort: 1-2 hours)

| Task | Details |
|:---|:---|
| Add `BoardComment` message to `agent.proto` | New protobuf types |
| Extend `BoardItem` with `comments` and `last_reason` | Wire to existing `GetBoard` |
| Add `GetBoardItem` RPC | Single-item detail endpoint |
| Add `/board/<sid>/item/<item_id>` to bridge | REST endpoint |

### Phase 4: UI (Effort: 3-4 hours)

| Task | Details |
|:---|:---|
| Expandable board cards with comment threads | Click to expand |
| Category icons and color coding | Visual differentiation |
| Staleness indicators (⚠️ badge) | Flag stale items |
| Item detail modal/panel | Full thread view |

---

## 9. What This Does NOT Do (Scope Boundaries)

To keep this from becoming sprawl itself:

1. **No hierarchical sub-tasks.** Items remain flat. If an agent needs to decompose work, it creates separate board items — subject to the 3-per-cycle throttle.
2. **No cross-item linking.** No "blocks" / "blocked-by" relationships. The causal DAG in the chatroom already captures execution dependencies.
3. **No agent-to-agent @mentions in comments.** Comments are documentation, not conversation. The election protocol handles inter-agent coordination.
4. **No human comment creation via UI (Phase 1).** Humans steer via anchors and chat messages. Board comments are agent-authored documentation. (Future: human comments could be added as a "directive" category.)
5. **No LLM-powered summarization.** Auto-summarization uses simple text concatenation, not an LLM call. Adding an LLM call to summarize comments would consume budget that should go toward actual work.

---

## 10. Success Metrics

How do we know this is working and not producing sprawl?

| Metric | Good Signal | Bad Signal |
|:---|:---|:---|
| Comments per item (avg) | 2-5 (focused documentation) | 8+ (agents spamming updates) |
| % of `done` items with conclusions | >80% (audit trail) | <30% (no documentation) |
| Items created vs. completed ratio | ~1:1 (steady throughput) | >3:1 (task sprawl, creation > completion) |
| Board consulted by agents (via `view`) | Regular reads before action | Never read (write-only graveyard) |
| Staleness rate | <10% items stale | >40% items abandoned |

If the bad signals emerge, the throttles should be tightened (lower budgets, stricter per-cycle limits) rather than removing the feature — the documentation value is too important to abandon, but the volume must be controlled.

---

## 11. Comparison: Before and After

### Before (Current)

```
Board:
⬜ [T-001] Investigate flaky test → David
🟡 [T-002] Fix import ordering → Carol
✅ [T-003] Fix null pointer → Carol

Q: "What did Carol find about the null pointer?"
A: ¯\_(ツ)_/¯  (scroll through 200 chatroom messages)
```

### After (With Comments)

```
Board:
⬜ [T-001] Investigate flaky test → David
   💬 3 · Last: [finding] "Race in conftest.py:42 yield fixture"
🟡 [T-002] Fix import ordering → Carol
   💬 1 · Last: [analysis] "Circular dep between utils.py and config.py"
✅ [T-003] Fix null pointer → Carol
   💬 2 · Last: [conclusion] "Guard added at parser.py:89. 47/47 tests pass."
   🔄 done: "All tests pass, fix verified on 3 edge cases."

Q: "What did Carol find about the null pointer?"
A: board(action="view", item_id="T-003")
   → Full thread with analysis → finding → conclusion
```

The board becomes a **structured knowledge base** that survives across election cycles, queryable by both agents and humans, bounded by enforced constraints against sprawl.
