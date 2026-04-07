/*
 * MetricsPipeline — Live Metrics Aggregation via Flink SQL.
 *
 * Computes running aggregates per session and writes to the live_metrics
 * PK table. GROUP BY session_id on a streaming source produces a retract
 * stream; the Fluss PK upsert sink handles retract/insert pairs by
 * updating the existing row in place.
 */
package com.containerclaw.telemetry;

public class MetricsPipeline {

    public static String getInsertSql() {
        return
            "INSERT INTO fluss_catalog.containerclaw.live_metrics\n"
            + "SELECT\n"
            + "    session_id,\n"
            + "    window_start,\n"
            + "    COUNT(*) AS total_messages,\n"
            + "    COUNT(CASE WHEN tool_name IS NOT NULL AND tool_name <> '' THEN 1 END) AS tool_calls,\n"
            + "    COUNT(CASE WHEN tool_success = true THEN 1 END) AS tool_successes\n"
            + "FROM TABLE(TUMBLE(TABLE temp_chatroom, DESCRIPTOR(pt), INTERVAL '5' SECONDS))\n"
            + "GROUP BY session_id, window_start, window_end";
    }
}
