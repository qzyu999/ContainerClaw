/*
 * DuckDBDialect — Flink JDBC dialect implementation for DuckDB.
 *
 * Flink's flink-connector-jdbc does not ship with a DuckDB dialect.
 * This custom dialect registers itself via Java SPI so that the JDBC
 * connector can handle 'jdbc:duckdb:' URLs.
 *
 * DuckDB is largely PostgreSQL-compatible for basic INSERT/UPSERT,
 * so this dialect extends the default behavior with DuckDB-specific
 * identifier quoting and upsert (INSERT OR REPLACE) support.
 */
package com.containerclaw.telemetry;

import org.apache.flink.connector.jdbc.converter.JdbcRowConverter;
import org.apache.flink.connector.jdbc.dialect.AbstractDialect;
import org.apache.flink.table.types.logical.LogicalTypeRoot;
import org.apache.flink.table.types.logical.RowType;

import java.io.Serializable;
import java.util.Arrays;
import java.util.EnumSet;
import java.util.Optional;
import java.util.Set;
import java.util.stream.Collectors;

public class DuckDBDialect extends AbstractDialect implements Serializable {

    private static final long serialVersionUID = 1L;

    @Override
    public String dialectName() {
        return "DuckDB";
    }

    @Override
    public JdbcRowConverter getRowConverter(RowType rowType) {
        return new org.apache.flink.connector.jdbc.converter.AbstractJdbcRowConverter(rowType) {
            private static final long serialVersionUID = 1L;

            @Override
            public String converterName() {
                return "DuckDB";
            }
        };
    }

    @Override
    public Optional<String> defaultDriverName() {
        return Optional.of("org.duckdb.DuckDBDriver");
    }

    @Override
    public String quoteIdentifier(String identifier) {
        return "\"" + identifier + "\"";
    }

    /**
     * DuckDB supports INSERT OR REPLACE which acts as upsert.
     */
    @Override
    public Optional<String> getUpsertStatement(
            String tableName, String[] fieldNames, String[] uniqueKeyFields) {
        String columns = Arrays.stream(fieldNames)
                .map(this::quoteIdentifier)
                .collect(Collectors.joining(", "));
        String placeholders = Arrays.stream(fieldNames)
                .map(f -> "?")
                .collect(Collectors.joining(", "));
        return Optional.of(
            "INSERT OR REPLACE INTO " + quoteIdentifier(tableName)
            + " (" + columns + ") VALUES (" + placeholders + ")"
        );
    }

    @Override
    public String getLimitClause(long limit) {
        return "LIMIT " + limit;
    }

    @Override
    public Set<LogicalTypeRoot> supportedTypes() {
        return EnumSet.of(
            LogicalTypeRoot.BOOLEAN,
            LogicalTypeRoot.TINYINT,
            LogicalTypeRoot.SMALLINT,
            LogicalTypeRoot.INTEGER,
            LogicalTypeRoot.BIGINT,
            LogicalTypeRoot.FLOAT,
            LogicalTypeRoot.DOUBLE,
            LogicalTypeRoot.DECIMAL,
            LogicalTypeRoot.VARCHAR,
            LogicalTypeRoot.CHAR,
            LogicalTypeRoot.DATE,
            LogicalTypeRoot.TIME_WITHOUT_TIME_ZONE,
            LogicalTypeRoot.TIMESTAMP_WITHOUT_TIME_ZONE,
            LogicalTypeRoot.TIMESTAMP_WITH_LOCAL_TIME_ZONE,
            LogicalTypeRoot.VARBINARY,
            LogicalTypeRoot.BINARY
        );
    }
}
