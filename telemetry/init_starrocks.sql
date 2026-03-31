-- ContainerClaw Telemetry — StarRocks Schema Initialization
-- Run once against StarRocks FE (port 9030) to create the analytical tables.
-- Usage: mysql -h starrocks-fe -P 9030 -u root < init_starrocks.sql

CREATE DATABASE IF NOT EXISTS containerclaw;
USE containerclaw;

-- ── DAG Edges ──────────────────────────────────────────────────────
-- Primary Key table for O(1) edge lookups during DAG visualization.
CREATE TABLE IF NOT EXISTS dag_edges (
    session_id   VARCHAR(128) NOT NULL,
    parent_id    VARCHAR(128) NOT NULL,
    child_id     VARCHAR(128) NOT NULL,
    status       VARCHAR(32) DEFAULT 'ACTIVE',
    created_at   BIGINT,
    updated_at   BIGINT
) ENGINE=OLAP
PRIMARY KEY(session_id, parent_id, child_id)
DISTRIBUTED BY HASH(session_id)
PROPERTIES (
    "enable_persistent_index" = "true"
);

-- ── Agent Context Snorkel ──────────────────────────────────────────
-- Primary Key table for O(1) context lookups per agent.
CREATE TABLE IF NOT EXISTS agent_context_snorkel (
    agent_id         VARCHAR(64) NOT NULL,
    session_id       VARCHAR(128) NOT NULL,
    run_id           VARCHAR(128) DEFAULT '',
    context_json     JSON,
    last_updated_at  BIGINT
) ENGINE=OLAP
PRIMARY KEY(agent_id, session_id)
DISTRIBUTED BY HASH(agent_id)
PROPERTIES (
    "enable_persistent_index" = "true"
);

-- ── Live Metrics ───────────────────────────────────────────────────
-- Primary Key table for fast sparkline queries by the HUD.
CREATE TABLE IF NOT EXISTS live_metrics (
    session_id       VARCHAR(128) NOT NULL,
    window_start     BIGINT NOT NULL,
    total_messages   INT DEFAULT 0,
    tool_calls       INT DEFAULT 0,
    tool_successes   INT DEFAULT 0,
    avg_latency_ms   DOUBLE DEFAULT 0.0
) ENGINE=OLAP
PRIMARY KEY(session_id, window_start)
DISTRIBUTED BY HASH(session_id)
PROPERTIES (
    "enable_persistent_index" = "true"
);
