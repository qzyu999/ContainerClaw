/*
 * ContainerClaw Telemetry — Flink Job Entry Point
 * Hybrid Snapshot + Delta Architecture (Fluss-Native)
 *
 * Reads from the chatroom log table (Fluss), computes:
 *   1. dag_summaries  — full DAG as JSON blob per session (PK table, O(1) lookup)
 *   2. dag_events     — individual edge updates (Log table, SSE tailing)
 *   3. live_metrics   — per-session running aggregates  (PK table, O(1) lookup)
 *
 * Zero external databases. Zero JDBC. Fluss is both source and sink.
 */
package com.containerclaw.telemetry;

import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.table.api.EnvironmentSettings;
import org.apache.flink.table.api.bridge.java.StreamTableEnvironment;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public class TelemetryJob {
    private static final Logger LOG = LoggerFactory.getLogger(TelemetryJob.class);

    private static final int MAX_RETRIES = 60;
    private static final long RETRY_INTERVAL_MS = 5000;

    public static void main(String[] args) throws Exception {
        LOG.info("=== ContainerClaw Telemetry Job Starting (Hybrid Snapshot+Delta) ===");

        // Load config
        String configPath = System.getenv("TELEMETRY_CONFIG");
        if (configPath == null || configPath.isEmpty()) {
            configPath = "/config/telemetry-config.yaml";
        }
        TelemetryConfig config = TelemetryConfig.load(configPath);
        LOG.info("Loaded config: fluss={}, database={}",
            config.getFlussBootstrapServers(), config.getFlussDatabase());

        // Set up Flink streaming + table environment
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.enableCheckpointing(30_000);
        StreamTableEnvironment tableEnv = StreamTableEnvironment.create(env,
                EnvironmentSettings.newInstance().inStreamingMode().build());

        // Register the Fluss catalog
        String createCatalog = String.format(
            "CREATE CATALOG fluss_catalog WITH ("
            + "'type' = 'fluss',"
            + "'bootstrap.servers' = '%s'"
            + ")",
            config.getFlussBootstrapServers()
        );
        tableEnv.executeSql(createCatalog);
        tableEnv.useCatalog("fluss_catalog");

        // Wait for the agent to create the database
        waitForDatabase(tableEnv, config.getFlussDatabase(), config.getFlussBootstrapServers());

        // Create sink tables (idempotent — IF NOT EXISTS)
        createSinkTables(tableEnv, config.getFlussDatabase());

        // Submit the pipelines as a single statement set
        var stmtSet = tableEnv.createStatementSet();
        stmtSet.addInsertSql(DagPipeline.getSnapshotInsertSql());
        stmtSet.addInsertSql(DagPipeline.getDeltaInsertSql());
        stmtSet.addInsertSql(MetricsPipeline.getInsertSql());

        LOG.info("Pipelines registered. Submitting statement set...");
        stmtSet.execute();
        LOG.info("=== ContainerClaw Telemetry Job Running ===");
    }

    /**
     * Create the sink tables that the pipelines write to.
     */
    private static void createSinkTables(StreamTableEnvironment tableEnv, String database) {
        LOG.info("Creating sink tables in database '{}'...", database);

        // PK table: full DAG snapshot per session (JSON blob)
        tableEnv.executeSql(
            "CREATE TABLE IF NOT EXISTS fluss_catalog." + database + ".dag_summaries ("
            + "    session_id STRING,"
            + "    edges_json STRING,"
            + "    edge_count BIGINT,"
            + "    updated_at BIGINT,"
            + "    PRIMARY KEY (session_id) NOT ENFORCED"
            + ") WITH ('bucket.num' = '4', 'bucket.key' = 'session_id')"
        );
        LOG.info("PK table dag_summaries ready.");

        // Log table: individual edge events for SSE streaming
        tableEnv.executeSql(
            "CREATE TABLE IF NOT EXISTS fluss_catalog." + database + ".dag_events ("
            + "    session_id STRING,"
            + "    parent_id STRING,"
            + "    child_id STRING,"
            + "    status STRING,"
            + "    updated_at BIGINT"
            + ") WITH ('bucket.num' = '4', 'bucket.key' = 'session_id')"
        );
        LOG.info("Log table dag_events ready.");

        // PK table: live metrics per session
        tableEnv.executeSql(
            "CREATE TABLE IF NOT EXISTS fluss_catalog." + database + ".live_metrics ("
            + "    session_id STRING,"
            + "    total_messages BIGINT,"
            + "    tool_calls BIGINT,"
            + "    tool_successes BIGINT,"
            + "    last_updated_at BIGINT,"
            + "    PRIMARY KEY (session_id) NOT ENFORCED"
            + ") WITH ('bucket.num' = '4', 'bucket.key' = 'session_id')"
        );
        LOG.info("PK table live_metrics ready.");
    }

    /**
     * Retry loop that waits for the Fluss database to become available.
     */
    private static void waitForDatabase(StreamTableEnvironment tableEnv, String database, String bootstrapServers) throws Exception {
        for (int attempt = 1; attempt <= MAX_RETRIES; attempt++) {
            try {
                tableEnv.useDatabase(database);
                LOG.info("Connected to Fluss database '{}' on attempt {}", database, attempt);
                return;
            } catch (Exception e) {
                LOG.warn("Attempt {}/{}: Database '{}' not ready — {}. Retrying in {}ms...",
                    attempt, MAX_RETRIES, database, e.getMessage(), RETRY_INTERVAL_MS);
                Thread.sleep(RETRY_INTERVAL_MS);
                try {
                    tableEnv.executeSql("DROP CATALOG IF EXISTS fluss_catalog");
                } catch (Exception ignored) {}
                tableEnv.executeSql(String.format(
                    "CREATE CATALOG fluss_catalog WITH ("
                    + "'type' = 'fluss',"
                    + "'bootstrap.servers' = '%s'"
                    + ")",
                    bootstrapServers
                ));
                tableEnv.useCatalog("fluss_catalog");
            }
        }
        throw new RuntimeException("Database '" + database + "' did not become available after " + MAX_RETRIES + " attempts");
    }
}
