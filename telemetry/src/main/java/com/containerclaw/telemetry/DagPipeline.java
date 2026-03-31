/*
 * DagPipeline — Final Spacetime DAG Generation.
 *
 * This version uses a Self-Joining stream with a 1-second forward-tolerance
 * to correctly link turns. 
 */
package com.containerclaw.telemetry;

public class DagPipeline {

    /**
     * Snapshot: Resolve causal links by self-joining chatroom log.
     */
    public static String getSnapshotInsertSql() {
        return
            "INSERT INTO fluss_catalog.containerclaw.dag_summaries\n"
            + "WITH causal_links AS (\n"
            + "    SELECT\n"
            + "        c1.session_id,\n"
            + "        CASE \n"
            + "            WHEN c2.event_id IS NULL THEN 'ROOT'\n"
            + "            ELSE CONCAT(c2.actor_id, '|', c2.event_id)\n"
            + "        END AS parent_id,\n"
            + "        CONCAT(c1.actor_id, '|', c1.event_id) AS child_id,\n"
            + "        COALESCE(c2.actor_id, 'Root') AS parent_label,\n"
            + "        c1.actor_id AS child_label,\n"
            + "        CASE\n"
            + "            WHEN c1.`type` IN ('finish', 'done', 'checkpoint') THEN 'DONE'\n"
            + "            WHEN c1.`type` = 'tool_call' THEN 'THINKING'\n"
            + "            ELSE 'ACTIVE'\n"
            + "        END AS status,\n"
            + "        c1.ts,\n"
            + "        ROW_NUMBER() OVER (PARTITION BY c1.event_id ORDER BY c2.ts DESC) as rn\n"
            + "    FROM fluss_catalog.containerclaw.chatroom c1\n"
            + "    LEFT JOIN fluss_catalog.containerclaw.chatroom c2 ON c1.session_id = c2.session_id \n"
            + "      AND c1.parent_actor = c2.actor_id\n"
            + "      -- Tolerance: Parent must be before or very close (1s) to account for bridge jitter\n"
            + "      AND c2.ts <= c1.ts + 1000\n"
            + ")\n"
            + "SELECT\n"
            + "    session_id,\n"
            + "    JSON_ARRAYAGG(\n"
            + "        JSON_OBJECT(\n"
            + "            'parent' VALUE parent_id,\n"
            + "            'child' VALUE child_id,\n"
            + "            'parent_label' VALUE parent_label,\n"
            + "            'child_label' VALUE child_label,\n"
            + "            'status' VALUE status,\n"
            + "            'ts' VALUE ts\n"
            + "        )\n"
            + "    ) AS edges_json,\n"
            + "    COUNT(*) AS edge_count,\n"
            + "    MAX(ts) AS updated_at\n"
            + "FROM causal_links\n"
            + "WHERE rn = 1\n"
            + "GROUP BY session_id";
    }

    /**
     * Delta: SSE updates using the same join logic.
     */
    public static String getDeltaInsertSql() {
        return
            "INSERT INTO fluss_catalog.containerclaw.dag_events\n"
            + "SELECT session_id, parent_id, child_id, status, ts AS updated_at\n"
            + "FROM (\n"
            + "    SELECT\n"
            + "        c1.session_id,\n"
            + "        CASE \n"
            + "            WHEN c2.event_id IS NULL THEN 'ROOT'\n"
            + "            ELSE CONCAT(c2.actor_id, '|', c2.event_id)\n"
            + "        END AS parent_id,\n"
            + "        CONCAT(c1.actor_id, '|', c1.event_id) AS child_id,\n"
            + "        CASE\n"
            + "            WHEN c1.`type` IN ('finish', 'done', 'checkpoint') THEN 'DONE'\n"
            + "            WHEN c1.`type` = 'tool_call' THEN 'THINKING'\n"
            + "            ELSE 'ACTIVE'\n"
            + "        END AS status,\n"
            + "        c1.ts,\n"
            + "        ROW_NUMBER() OVER (PARTITION BY c1.event_id ORDER BY c2.ts DESC) as rn\n"
            + "    FROM fluss_catalog.containerclaw.chatroom c1\n"
            + "    LEFT JOIN fluss_catalog.containerclaw.chatroom c2 ON c1.session_id = c2.session_id \n"
            + "      AND c1.parent_actor = c2.actor_id\n"
            + "      AND c2.ts <= c1.ts + 1000\n"
            + ") WHERE rn = 1";
    }

    // Unchanged actor heads insert if still needed, but not used in current snapshot/delta
    public static String getActorHeadsInsertSql() {
        return "INSERT INTO fluss_catalog.containerclaw.actor_heads SELECT session_id, actor_id, event_id, ts FROM fluss_catalog.containerclaw.chatroom";
    }
}
