/*
 * DagPipeline — Deterministic DAG via Linear Backbone with Tiering.
 *
 * Replaces the old heuristic self-join approach with a simple projection.
 * Causality is now recorded at event creation time via parent_event_id
 * and edge_type fields in the chatroom schema. The pipeline merely
 * projects these fields into the dag_edges sink table.
 */
package com.containerclaw.telemetry;

public class DagPipeline {

    /**
     * Edge projection: deterministic DAG edges from chatroom log.
     *
     * Each chatroom event carries parent_event_id (the UUID of its causal
     * predecessor) and edge_type (SEQUENTIAL, SPAWN, RETURN, ROOT).
     * This query simply projects those fields into the dag_edges PK table.
     *
     * No joins. No windows. No heuristics.
     */
    public static String getEdgesInsertSql() {
        return
            "INSERT INTO fluss_catalog.containerclaw.dag_edges\n"
            + "SELECT\n"
            + "    session_id,\n"
            + "    CASE\n"
            + "        WHEN parent_event_id IS NULL OR parent_event_id = '' THEN 'ROOT'\n"
            + "        ELSE parent_event_id\n"
            + "    END AS parent_id,\n"
            + "    event_id AS child_id,\n"
            + "    actor_id AS child_actor,\n"
            + "    COALESCE(edge_type, 'SEQUENTIAL') AS edge_type,\n"
            + "    CASE\n"
            + "        WHEN `type` IN ('finish', 'done', 'checkpoint') THEN 'DONE'\n"
            + "        WHEN `type` = 'action' THEN 'THINKING'\n"
            + "        ELSE 'ACTIVE'\n"
            + "    END AS status,\n"
            + "    ts AS updated_at\n"
            + "FROM fluss_catalog.containerclaw.chatroom";
    }

    // Actor heads projection — unchanged from before
    public static String getActorHeadsInsertSql() {
        return "INSERT INTO fluss_catalog.containerclaw.actor_heads SELECT session_id, actor_id, event_id, ts FROM fluss_catalog.containerclaw.chatroom";
    }

    // ── Legacy methods (kept for reference, no longer registered) ──

    /**
     * @deprecated Replaced by getEdgesInsertSql(). The self-join approach
     * produced non-deterministic results because parent_actor + timestamp
     * proximity is not a unique match.
     */
    @Deprecated
    public static String getSnapshotInsertSql() {
        return "-- DEPRECATED: Use getEdgesInsertSql() instead";
    }

    /**
     * @deprecated Replaced by getEdgesInsertSql().
     */
    @Deprecated
    public static String getDeltaInsertSql() {
        return "-- DEPRECATED: Use getEdgesInsertSql() instead";
    }
}
