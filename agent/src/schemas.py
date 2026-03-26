"""
Fluss table schemas — single source of truth.

All Fluss table schemas used by ContainerClaw are defined here.
Every component that reads from or writes to Fluss must import
schemas from this module rather than defining them inline.
"""

import pyarrow as pa

# ── Chatroom Log Table ──────────────────────────────────────────────
# Append-only log of all messages, tool outputs, and agent activity.
# Bucket key: session_id
CHATROOM_SCHEMA = pa.schema([
    pa.field("event_id", pa.string()),      # UUID — primary dedup key
    pa.field("session_id", pa.string()),
    pa.field("ts", pa.int64()),
    pa.field("actor_id", pa.string()),
    pa.field("content", pa.string()),
    pa.field("type", pa.string()),
    pa.field("tool_name", pa.string()),
    pa.field("tool_success", pa.bool_()),
    pa.field("parent_actor", pa.string()),
])

# ── Sessions Table ──────────────────────────────────────────────────
# Session metadata (log table, will migrate to PK table in Phase 3).
# Bucket key: session_id
SESSIONS_SCHEMA = pa.schema([
    pa.field("session_id", pa.string()),
    pa.field("title", pa.string()),
    pa.field("created_at", pa.int64()),
    pa.field("last_active_at", pa.int64()),
])

# ── Board Events Table ──────────────────────────────────────────────
# Append-only log of project board mutations (create, update, delete).
# Bucket key: session_id
BOARD_EVENTS_SCHEMA = pa.schema([
    pa.field("session_id", pa.string()),
    pa.field("ts", pa.int64()),
    pa.field("action", pa.string()),
    pa.field("item_id", pa.string()),
    pa.field("item_type", pa.string()),
    pa.field("title", pa.string()),
    pa.field("description", pa.string()),
    pa.field("status", pa.string()),
    pa.field("assigned_to", pa.string()),
    pa.field("actor", pa.string()),
])

# ── Table Paths ─────────────────────────────────────────────────────
# Centralized table path constants (database.table)
DATABASE = "containerclaw"
CHATROOM_TABLE = "chatroom"
SESSIONS_TABLE = "sessions"
BOARD_EVENTS_TABLE = "board_events"

# ── Bucket Configuration ────────────────────────────────────────────
DEFAULT_BUCKET_COUNT = 16
BUCKET_KEY = ["session_id"]
