/*
 * SinkRegistrar — Registers JDBC sink tables in the Flink SQL environment.
 *
 * This is the ONLY class that knows about the sink engine. It registers
 * Flink SQL tables (dag_edges_sink, metrics_sink) with the appropriate
 * JDBC connector configuration. The pipeline SQL is identical regardless
 * of engine — only the WITH (...) clause differs.
 *
 * For DuckDB: Also initializes the physical database tables before
 * registering the Flink SQL sinks, since DuckDB doesn't auto-create tables.
 */
package com.containerclaw.telemetry;

import org.apache.flink.table.api.bridge.java.StreamTableEnvironment;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.Statement;

public class SinkRegistrar {
    private static final Logger LOG = LoggerFactory.getLogger(SinkRegistrar.class);

    /**
     * Register all JDBC sink tables based on the config.
     */
    public static void registerAll(StreamTableEnvironment tableEnv, TelemetryConfig config) {
        // For DuckDB, initialize the physical tables first
        if ("duckdb".equals(config.getSinkEngine())) {
            initDuckDbTables(config.getJdbcUrl());
        }

        // Switch to default_catalog for sink tables (JDBC, not Fluss)
        tableEnv.useCatalog("default_catalog");
        tableEnv.useDatabase("default_database");

        registerDagEdgesSink(tableEnv, config);
        registerMetricsSink(tableEnv, config);

        // Switch back to Fluss catalog for source reads
        tableEnv.useCatalog("fluss_catalog");
        tableEnv.useDatabase(config.getFlussDatabase());
    }

    /**
     * Creates the DuckDB tables if they don't exist.
     * DuckDB JDBC sinks require pre-existing tables.
     */
    private static void initDuckDbTables(String jdbcUrl) {
        LOG.info("Initializing DuckDB tables at: {}", jdbcUrl);
        try {
            Class.forName("org.duckdb.DuckDBDriver");
            try (Connection conn = DriverManager.getConnection(jdbcUrl);
                 Statement stmt = conn.createStatement()) {

                stmt.execute(
                    "CREATE TABLE IF NOT EXISTS dag_edges ("
                    + "session_id VARCHAR NOT NULL,"
                    + "parent_id VARCHAR NOT NULL,"
                    + "child_id VARCHAR NOT NULL,"
                    + "status VARCHAR DEFAULT 'ACTIVE',"
                    + "created_at BIGINT,"
                    + "updated_at BIGINT"
                    + ")"
                );

                stmt.execute(
                    "CREATE TABLE IF NOT EXISTS live_metrics ("
                    + "session_id VARCHAR NOT NULL,"
                    + "window_start BIGINT NOT NULL,"
                    + "total_messages BIGINT DEFAULT 0,"
                    + "tool_calls BIGINT DEFAULT 0,"
                    + "tool_successes BIGINT DEFAULT 0"
                    + ")"
                );

                LOG.info("DuckDB tables initialized successfully");
            }
        } catch (Exception e) {
            LOG.error("Failed to initialize DuckDB tables: {}", e.getMessage(), e);
            throw new RuntimeException("Cannot initialize DuckDB", e);
        }
    }

    private static void registerDagEdgesSink(StreamTableEnvironment tableEnv, TelemetryConfig config) {
        String sql = String.format(
            "CREATE TABLE default_catalog.default_database.dag_edges_sink (\n"
            + "    session_id STRING,\n"
            + "    parent_id STRING,\n"
            + "    child_id STRING,\n"
            + "    status STRING,\n"
            + "    created_at BIGINT,\n"
            + "    updated_at BIGINT,\n"
            + "    PRIMARY KEY (session_id, parent_id, child_id) NOT ENFORCED\n"
            + ") WITH (\n"
            + "    'connector' = 'jdbc',\n"
            + "    'url' = '%s',\n"
            + "    'table-name' = 'dag_edges',\n"
            + "    'driver' = '%s'%s\n"
            + ")",
            config.getJdbcUrl(),
            config.getJdbcDriverClass(),
            getAuthClause(config)
        );
        LOG.info("Registering dag_edges_sink");
        tableEnv.executeSql(sql);
    }

    private static void registerMetricsSink(StreamTableEnvironment tableEnv, TelemetryConfig config) {
        String sql = String.format(
            "CREATE TABLE default_catalog.default_database.metrics_sink (\n"
            + "    session_id STRING,\n"
            + "    window_start BIGINT,\n"
            + "    total_messages BIGINT,\n"
            + "    tool_calls BIGINT,\n"
            + "    tool_successes BIGINT,\n"
            + "    PRIMARY KEY (session_id, window_start) NOT ENFORCED\n"
            + ") WITH (\n"
            + "    'connector' = 'jdbc',\n"
            + "    'url' = '%s',\n"
            + "    'table-name' = 'live_metrics',\n"
            + "    'driver' = '%s'%s\n"
            + ")",
            config.getJdbcUrl(),
            config.getJdbcDriverClass(),
            getAuthClause(config)
        );
        LOG.info("Registering metrics_sink");
        tableEnv.executeSql(sql);
    }

    /**
     * Returns the JDBC auth clause for StarRocks, or empty for DuckDB.
     */
    private static String getAuthClause(TelemetryConfig config) {
        if ("starrocks".equals(config.getSinkEngine())) {
            return String.format(
                ",\n    'username' = '%s',\n    'password' = '%s'",
                config.getJdbcUsername(),
                config.getJdbcPassword()
            );
        }
        return "";
    }
}
