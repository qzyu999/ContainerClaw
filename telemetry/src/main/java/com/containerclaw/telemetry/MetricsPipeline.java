/*
 * MetricsPipeline — Live Metrics Aggregation via Flink SQL.
 *
 * Computes running aggregates per session:
 *   - total_messages: COUNT(*) of all events
 *   - tool_calls: COUNT of events where tool_name is non-empty
 *   - tool_successes: COUNT of events where tool_success is true
 *
 * Since the chatroom.ts field is a BIGINT (epoch millis) and Flink's
 * TUMBLE window requires a proper time attribute with watermarks, we
 * use a simpler approach: bucket events into 10-second floors using
 * integer arithmetic (ts / 10000 * 10000) and GROUP BY the bucket.
 *
 * This provides the same per-window aggregation without requiring
 * schema changes to the source table.
 */
package com.containerclaw.telemetry;

public class MetricsPipeline {

    /**
     * Returns the INSERT SQL that reads from the Fluss chatroom table
     * and writes aggregated metrics to the JDBC sink.
     */
    public static String getInsertSql() {
        return
            "INSERT INTO default_catalog.default_database.metrics_sink\n"
            + "SELECT\n"
            + "    session_id,\n"
            + "    (ts / 10000) * 10000 AS window_start,\n"
            + "    COUNT(*) AS total_messages,\n"
            + "    COUNT(CASE WHEN tool_name IS NOT NULL AND tool_name <> '' THEN 1 END) AS tool_calls,\n"
            + "    COUNT(CASE WHEN tool_success = true THEN 1 END) AS tool_successes\n"
            + "FROM fluss_catalog.containerclaw.chatroom\n"
            + "GROUP BY session_id, (ts / 10000) * 10000";
    }
}
