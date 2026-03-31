FROM maven:3.9-eclipse-temurin-11 AS builder

WORKDIR /build
COPY pom.xml .
# Cache dependencies first
RUN mvn dependency:go-offline -B || true

COPY src/ src/
RUN mvn package -DskipTests -B

# ── Final: Flink base image with the job JAR ──────────────────────
FROM flink:1.20

# Copy the shaded fat JAR into Flink's usrlib (auto-loaded on job submission)
COPY --from=builder /build/target/telemetry-flink-job-1.0-SNAPSHOT.jar /opt/flink/usrlib/telemetry-job.jar
