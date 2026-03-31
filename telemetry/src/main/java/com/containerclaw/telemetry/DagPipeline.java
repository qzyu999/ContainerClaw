/*
 * DagPipeline — DAG Edge Reconstruction via Flink SQL.
 *
 * Extracts parent-child agent relationships from chatroom events.
 * Every event with a non-empty parent_actor creates or updates a DAG edge.
 * Status transitions:
 *   - type='output' with parent_actor → ACTIVE edge (agent is producing)
 *   - type='tool_call' → THINKING (agent is using a tool)
 *   - type='finish' or type='done' → DONE
 *   - All others → ACTIVE
 *
 * The query upserts into the JDBC sink on the primary key
 * (session_id, parent_id, child_id), so repeated events update status.
 */
package com.containerclaw.telemetry;

public class DagPipeline {

    /**
     * Returns the INSERT SQL that reads from the Fluss chatroom table
     * and writes DAG edges to the JDBC sink.
     *
     * This SQL is engine-agnostic — it runs identically against DuckDB
     * and StarRocks because the sink table is pre-registered by SinkRegistrar.
     */
    public static String getInsertSql() {
        return
            "INSERT INTO default_catalog.default_database.dag_edges_sink\n"
            + "SELECT\n"
            + "    session_id,\n"
            + "    parent_actor AS parent_id,\n"
            + "    actor_id AS child_id,\n"
            + "    CASE\n"
            + "        WHEN `type` IN ('finish', 'done', 'checkpoint') THEN 'DONE'\n"
            + "        WHEN `type` = 'tool_call' THEN 'THINKING'\n"
            + "        ELSE 'ACTIVE'\n"
            + "    END AS status,\n"
            + "    ts AS created_at,\n"
            + "    ts AS updated_at\n"
            + "FROM fluss_catalog.containerclaw.chatroom\n"
            + "WHERE parent_actor IS NOT NULL AND parent_actor <> ''";
    }
}
