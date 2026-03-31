/*
 * ContainerClaw Telemetry — Flink Job Entry Point
 *
 * This job consumes the chatroom event stream from Fluss via the Flink SQL
 * catalog integration and materializes derived views into a pluggable
 * JDBC sink (DuckDB for local, StarRocks for enterprise):
 *
 *   1. DAG Edges     — parent/child agent relationships
 *   2. Live Metrics  — bucketed message/tool aggregates
 *
 * The core agent runtime is completely unaware of this job. It simply
 * writes to Fluss; this job observes the stream as a side-car.
 *
 * STARTUP: The Flink job retries connecting to the Fluss database on a
 * 5-second interval. The agent creates the database and tables on first
 * boot, so there's a race condition at startup. The retry loop ensures
 * the telemetry job survives this without manual intervention.
 */
package com.containerclaw.telemetry;

import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.table.api.EnvironmentSettings;
import org.apache.flink.table.api.bridge.java.StreamTableEnvironment;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public class TelemetryJob {
    private static final Logger LOG = LoggerFactory.getLogger(TelemetryJob.class);

    private static final int MAX_RETRIES = 60;       // 5 minutes total
    private static final long RETRY_INTERVAL_MS = 5000; // 5 seconds

    public static void main(String[] args) throws Exception {
        LOG.info("=== ContainerClaw Telemetry Job Starting ===");

        // Load config from the mounted YAML file
        String configPath = System.getenv("TELEMETRY_CONFIG");
        if (configPath == null || configPath.isEmpty()) {
            configPath = "/config/telemetry-config.yaml";
        }
        TelemetryConfig config = TelemetryConfig.load(configPath);
        LOG.info("Loaded config: engine={}, fluss={}", config.getSinkEngine(), config.getFlussBootstrapServers());

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

        // Wait for the agent to create the database + tables in Fluss.
        // The agent initializes Fluss on first boot, which may take 30+ seconds.
        waitForDatabase(tableEnv, config.getFlussDatabase(), config.getFlussBootstrapServers());

        // Register the JDBC sink tables based on the configured engine
        SinkRegistrar.registerAll(tableEnv, config);
        LOG.info("JDBC sink tables registered for engine: {}", config.getSinkEngine());

        // Submit the pipelines as a single statement set
        var stmtSet = tableEnv.createStatementSet();
        stmtSet.addInsertSql(DagPipeline.getInsertSql());
        stmtSet.addInsertSql(MetricsPipeline.getInsertSql());

        LOG.info("Pipelines registered. Submitting statement set...");
        stmtSet.execute();
        LOG.info("=== ContainerClaw Telemetry Job Running ===");
    }

    /**
     * Retry loop that waits for the Fluss database to become available.
     * The agent creates the 'containerclaw' database on first boot,
     * but the Flink job may start before that completes.
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
                // Re-register catalog to refresh metadata
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
