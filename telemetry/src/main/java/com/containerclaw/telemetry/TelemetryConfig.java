/*
 * TelemetryConfig — YAML configuration loader for the Flink telemetry job.
 *
 * Reads the telemetry-config.yaml mounted into the Flink container and
 * provides typed access to source (Fluss) and sink (DuckDB/StarRocks) settings.
 */
package com.containerclaw.telemetry;

import org.yaml.snakeyaml.Yaml;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.FileInputStream;
import java.io.InputStream;
import java.io.Serializable;
import java.util.Map;

public class TelemetryConfig implements Serializable {
    private static final Logger LOG = LoggerFactory.getLogger(TelemetryConfig.class);

    private final String sinkEngine;
    private final String jdbcUrl;
    private final String jdbcUsername;
    private final String jdbcPassword;

    private final String flussBootstrapServers;
    private final String flussDatabase;
    private final String flussTable;

    private final int stateTtlHours;
    private final int snorkelMaxMessages;
    private final int metricsWindowSeconds;

    private TelemetryConfig(
            String sinkEngine, String jdbcUrl, String jdbcUsername, String jdbcPassword,
            String flussBootstrapServers, String flussDatabase, String flussTable,
            int stateTtlHours, int snorkelMaxMessages, int metricsWindowSeconds) {
        this.sinkEngine = sinkEngine;
        this.jdbcUrl = jdbcUrl;
        this.jdbcUsername = jdbcUsername;
        this.jdbcPassword = jdbcPassword;
        this.flussBootstrapServers = flussBootstrapServers;
        this.flussDatabase = flussDatabase;
        this.flussTable = flussTable;
        this.stateTtlHours = stateTtlHours;
        this.snorkelMaxMessages = snorkelMaxMessages;
        this.metricsWindowSeconds = metricsWindowSeconds;
    }

    @SuppressWarnings("unchecked")
    public static TelemetryConfig load(String path) {
        try (InputStream in = new FileInputStream(path)) {
            Yaml yaml = new Yaml();
            Map<String, Object> root = yaml.load(in);

            Map<String, Object> sink = (Map<String, Object>) root.get("sink");
            String engine = (String) sink.getOrDefault("engine", "duckdb");

            String jdbcUrl;
            String username = "";
            String password = "";

            if ("starrocks".equals(engine)) {
                Map<String, Object> sr = (Map<String, Object>) sink.get("starrocks");
                jdbcUrl = (String) sr.get("jdbc_url");
                username = (String) sr.getOrDefault("username", "root");
                password = (String) sr.getOrDefault("password", "");
            } else {
                Map<String, Object> dk = (Map<String, Object>) sink.get("duckdb");
                jdbcUrl = (String) dk.get("jdbc_url");
            }

            Map<String, Object> source = (Map<String, Object>) root.get("source");
            Map<String, Object> fluss = (Map<String, Object>) source.get("fluss");

            Map<String, Object> job = (Map<String, Object>) root.getOrDefault("job", Map.of());

            return new TelemetryConfig(
                engine, jdbcUrl, username, password,
                (String) fluss.get("bootstrap_servers"),
                (String) fluss.getOrDefault("database", "containerclaw"),
                (String) fluss.getOrDefault("table", "chatroom"),
                ((Number) job.getOrDefault("state_ttl_hours", 4)).intValue(),
                ((Number) job.getOrDefault("snorkel_max_messages", 100)).intValue(),
                ((Number) job.getOrDefault("metrics_window_seconds", 1)).intValue()
            );
        } catch (Exception e) {
            LOG.error("Failed to load telemetry config from {}: {}", path, e.getMessage());
            throw new RuntimeException("Cannot load telemetry config", e);
        }
    }

    // ── Accessors ──────────────────────────────────────────────────

    public String getSinkEngine() { return sinkEngine; }
    public String getJdbcUrl() { return jdbcUrl; }
    public String getJdbcUsername() { return jdbcUsername; }
    public String getJdbcPassword() { return jdbcPassword; }
    public String getFlussBootstrapServers() { return flussBootstrapServers; }
    public String getFlussDatabase() { return flussDatabase; }
    public String getFlussTable() { return flussTable; }
    public int getStateTtlHours() { return stateTtlHours; }
    public int getSnorkelMaxMessages() { return snorkelMaxMessages; }
    public int getMetricsWindowSeconds() { return metricsWindowSeconds; }

    /**
     * Returns the JDBC driver class name for the configured engine.
     */
    public String getJdbcDriverClass() {
        return "starrocks".equals(sinkEngine)
            ? "com.mysql.cj.jdbc.Driver"
            : "org.duckdb.DuckDBDriver";
    }
}
