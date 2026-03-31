/*
 * TelemetryConfig — Simplified YAML config for the Fluss-native pipeline.
 *
 * No JDBC, no sink engine selection. The only config needed is the Fluss
 * bootstrap servers and job parameters.
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

    private final String flussBootstrapServers;
    private final String flussDatabase;
    private final int stateTtlHours;

    private TelemetryConfig(String flussBootstrapServers, String flussDatabase, int stateTtlHours) {
        this.flussBootstrapServers = flussBootstrapServers;
        this.flussDatabase = flussDatabase;
        this.stateTtlHours = stateTtlHours;
    }

    @SuppressWarnings("unchecked")
    public static TelemetryConfig load(String path) {
        try (InputStream in = new FileInputStream(path)) {
            Yaml yaml = new Yaml();
            Map<String, Object> root = yaml.load(in);

            Map<String, Object> source = (Map<String, Object>) root.get("source");
            Map<String, Object> fluss = (Map<String, Object>) source.get("fluss");

            Map<String, Object> job = (Map<String, Object>) root.getOrDefault("job", Map.of());

            return new TelemetryConfig(
                (String) fluss.get("bootstrap_servers"),
                (String) fluss.getOrDefault("database", "containerclaw"),
                ((Number) job.getOrDefault("state_ttl_hours", 4)).intValue()
            );
        } catch (Exception e) {
            LOG.error("Failed to load telemetry config from {}: {}", path, e.getMessage());
            throw new RuntimeException("Cannot load telemetry config", e);
        }
    }

    public String getFlussBootstrapServers() { return flussBootstrapServers; }
    public String getFlussDatabase() { return flussDatabase; }
    public int getStateTtlHours() { return stateTtlHours; }
}
