/*
 * ContainerClaw Telemetry — Flink Job Entry Point
 * Hybrid Snapshot + Delta Architecture (Fluss-Native)
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
        stmtSet.addInsertSql(DagPipeline.getActorHeadsInsertSql());
        stmtSet.addInsertSql(DagPipeline.getEdgesInsertSql());
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

        // PK table: track the most recent turn for each actor per session
        tableEnv.executeSql(
            "CREATE TABLE IF NOT EXISTS fluss_catalog." + database + ".actor_heads ("
            + "    session_id STRING,"
            + "    actor_id STRING,"
            + "    last_event_id STRING,"
            + "    last_ts BIGINT,"
            + "    PRIMARY KEY (session_id, actor_id) NOT ENFORCED"
            + ") WITH ('bucket.num' = '4', 'bucket.key' = 'session_id,actor_id')"
        );

        // PK table: deterministic DAG edges from chatroom causal metadata
        tableEnv.executeSql(
            "CREATE TABLE IF NOT EXISTS fluss_catalog." + database + ".dag_edges ("
            + "    session_id STRING,"
            + "    parent_id STRING,"
            + "    child_id STRING,"
            + "    child_label STRING,"
            + "    edge_type STRING,"
            + "    status STRING,"
            + "    updated_at BIGINT,"
            + "    PRIMARY KEY (session_id, child_id) NOT ENFORCED"
            + ") WITH ('bucket.num' = '4', 'bucket.key' = 'session_id')"
        );

        // LOG table: live metrics per window
        // Dropped first because we are migrating it from a PK table to a LOG table
        tableEnv.executeSql("DROP TABLE IF EXISTS fluss_catalog." + database + ".live_metrics");
        tableEnv.executeSql(
            "CREATE TABLE fluss_catalog." + database + ".live_metrics ("
            + "    session_id STRING,"
            + "    window_start TIMESTAMP(3),"
            + "    total_messages BIGINT,"
            + "    tool_calls BIGINT,"
            + "    tool_successes BIGINT"
            + ") WITH ('bucket.num' = '4', 'bucket.key' = 'session_id')"
        );

        // Create a temporary view to assign processing time so we can use TUMBLE windows
        tableEnv.executeSql(
            "CREATE TEMPORARY VIEW temp_chatroom AS "
            + "SELECT *, PROCTIME() AS pt FROM fluss_catalog." + database + ".chatroom"
        );
    }

    private static void waitForDatabase(StreamTableEnvironment tableEnv, String database, String bootstrapServers) throws Exception {
        for (int attempt = 1; attempt <= MAX_RETRIES; attempt++) {
            try {
                tableEnv.useDatabase(database);
                LOG.info("Connected to Fluss database '{}'", database);
                return;
            } catch (Exception e) {
                LOG.warn("Attempt {}/{}: Database '{}' not ready. Retrying in {}ms...",
                    attempt, MAX_RETRIES, database, RETRY_INTERVAL_MS);
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
        throw new RuntimeException("Database '" + database + "' not available");
    }
}
