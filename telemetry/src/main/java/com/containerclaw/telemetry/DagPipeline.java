/*
 * DagPipeline — Hybrid Snapshot + Delta DAG Reconstruction.
 *
 * Two outputs from the same chatroom source:
 *
 * 1. SNAPSHOT (dag_summaries PK table):
 *    Uses JSON_ARRAYAGG + JSON_OBJECT + GROUP BY session_id to aggregate
 *    ALL edges into a single JSON blob per session. Flink's retract stream
 *    keeps this up-to-date via upsert on the PK table.
 *
 * 2. DELTA (dag_events Log table):
 *    Simple INSERT that appends individual edge events for real-time
 *    SSE tailing by the bridge.
 */
package com.containerclaw.telemetry;

public class DagPipeline {

    /**
     * Snapshot: Aggregate all edges into a JSON array per session.
     * Written to dag_summaries PK table (key=session_id).
     * Each chatroom event with parent_actor produces/updates the full snapshot.
     */
    public static String getSnapshotInsertSql() {
        return
            "INSERT INTO fluss_catalog.containerclaw.dag_summaries\n"
            + "SELECT\n"
            + "    session_id,\n"
            + "    JSON_ARRAYAGG(\n"
            + "        JSON_OBJECT(\n"
            + "            'parent' VALUE parent_actor,\n"
            + "            'child' VALUE actor_id,\n"
            + "            'status' VALUE CASE\n"
            + "                WHEN `type` IN ('finish', 'done', 'checkpoint') THEN 'DONE'\n"
            + "                WHEN `type` = 'tool_call' THEN 'THINKING'\n"
            + "                ELSE 'ACTIVE'\n"
            + "            END\n"
            + "        )\n"
            + "    ) AS edges_json,\n"
            + "    COUNT(*) AS edge_count,\n"
            + "    MAX(ts) AS updated_at\n"
            + "FROM fluss_catalog.containerclaw.chatroom\n"
            + "WHERE parent_actor IS NOT NULL AND parent_actor <> ''\n"
            + "GROUP BY session_id";
    }

    /**
     * Delta: Append individual edge events to the dag_events log table.
     * Used by the bridge for real-time SSE streaming to the UI.
     */
    public static String getDeltaInsertSql() {
        return
            "INSERT INTO fluss_catalog.containerclaw.dag_events\n"
            + "SELECT\n"
            + "    session_id,\n"
            + "    parent_actor AS parent_id,\n"
            + "    actor_id AS child_id,\n"
            + "    CASE\n"
            + "        WHEN `type` IN ('finish', 'done', 'checkpoint') THEN 'DONE'\n"
            + "        WHEN `type` = 'tool_call' THEN 'THINKING'\n"
            + "        ELSE 'ACTIVE'\n"
            + "    END AS status,\n"
            + "    ts AS updated_at\n"
            + "FROM fluss_catalog.containerclaw.chatroom\n"
            + "WHERE parent_actor IS NOT NULL AND parent_actor <> ''";
    }
}
