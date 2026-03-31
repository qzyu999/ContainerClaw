/*
 * DagPipeline — DAG Edge Reconstruction via Flink SQL.
 *
 * Reads from the chatroom log table and writes to the dag_edges PK table.
 * Since the PK is (session_id, parent_id, child_id), repeated events
 * for the same edge update status and updated_at in place via upsert.
 */
package com.containerclaw.telemetry;

public class DagPipeline {

    public static String getInsertSql() {
        return
            "INSERT INTO fluss_catalog.containerclaw.dag_edges\n"
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
