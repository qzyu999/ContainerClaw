-- ContainerClaw Telemetry — DuckDB Schema Initialization
-- Run once to create the analytical tables in the local DuckDB file.
-- Usage: duckdb /state/telemetry.duckdb < init_duckdb.sql

-- ── DAG Edges ──────────────────────────────────────────────────────
-- Stores the parent-child relationships between agents in the swarm.
-- Flink's DAG Reconstructor upserts edges as events arrive.
CREATE TABLE IF NOT EXISTS dag_edges (
    session_id   VARCHAR NOT NULL,
    parent_id    VARCHAR NOT NULL,
    child_id     VARCHAR NOT NULL,
    status       VARCHAR DEFAULT 'ACTIVE',   -- ACTIVE, THINKING, DONE
    created_at   BIGINT,                     -- ms timestamp
    updated_at   BIGINT,                     -- ms timestamp
    PRIMARY KEY (session_id, parent_id, child_id)
);

-- ── Agent Context Snorkel ──────────────────────────────────────────
-- The materialized "context window" for each agent. Flink upserts
-- the full JSON context on every new message so the UI can render
-- the exact state the agent is currently working with.
CREATE TABLE IF NOT EXISTS agent_context_snorkel (
    agent_id         VARCHAR NOT NULL,
    session_id       VARCHAR NOT NULL,
    run_id           VARCHAR DEFAULT '',
    context_json     JSON,                   -- Full context window as JSON array
    last_updated_at  BIGINT,                 -- ms timestamp
    PRIMARY KEY (agent_id, session_id)
);

-- ── Live Metrics ───────────────────────────────────────────────────
-- 1-second tumbling window aggregates for sparklines and system health.
CREATE TABLE IF NOT EXISTS live_metrics (
    session_id       VARCHAR NOT NULL,
    window_start     BIGINT NOT NULL,        -- ms timestamp of window start
    total_messages   INTEGER DEFAULT 0,
    tool_calls       INTEGER DEFAULT 0,
    tool_successes   INTEGER DEFAULT 0,
    avg_latency_ms   DOUBLE DEFAULT 0.0,
    PRIMARY KEY (session_id, window_start)
);
