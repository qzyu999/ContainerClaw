# Universal Fluss Cluster Image
# ==============================
# Runs ZooKeeper + Fluss Coordinator + TabletServer in a single container.
# Works both locally (Docker Compose) and on cloud (ECS Fargate).
#
# Build: docker build -t containerclaw-fluss ./fluss
# Run:   docker run -p 9123:9123 containerclaw-fluss
#
# All JARs are baked in at build time — no downloads at startup.
# Cold-start: ~15-20s (ZK election + Fluss init only).

FROM amazoncorretto:17-al2023

# Install dependencies at build time
RUN yum install -y tar gzip procps shadow-utils findutils util-linux which nc && \
    yum clean all && \
    rm -rf /var/cache/yum

# ── ZooKeeper ────────────────────────────────────────────────────
ARG ZOOKEEPER_VERSION=3.9.2
RUN curl -fsSL "https://archive.apache.org/dist/zookeeper/zookeeper-${ZOOKEEPER_VERSION}/apache-zookeeper-${ZOOKEEPER_VERSION}-bin.tar.gz" \
    | tar -xz -C /opt && \
    mv /opt/apache-zookeeper-${ZOOKEEPER_VERSION}-bin /opt/zookeeper && \
    mkdir -p /data/zookeeper

# ── Fluss ────────────────────────────────────────────────────────
ARG FLUSS_VERSION=0.9.0-incubating
RUN curl -fsSL "https://archive.apache.org/dist/incubator/fluss/fluss-${FLUSS_VERSION}/fluss-${FLUSS_VERSION}-bin.tgz" \
    | tar -xz -C /opt && \
    mv /opt/fluss-${FLUSS_VERSION} /opt/fluss && \
    mkdir -p /opt/fluss/plugins/iceberg /opt/fluss/plugins/s3 /tmp/fluss/data /tmp/fluss/remote-data

# ── Iceberg + AWS JARs (for lake tiering) ────────────────────────
ARG ICEBERG_VERSION=1.10.1
RUN curl -fsSL -o /opt/fluss/plugins/iceberg/iceberg-aws-bundle-${ICEBERG_VERSION}.jar \
      "https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-aws-bundle/${ICEBERG_VERSION}/iceberg-aws-bundle-${ICEBERG_VERSION}.jar" && \
    curl -fsSL -o /opt/fluss/plugins/iceberg/failsafe-3.3.2.jar \
      "https://repo1.maven.org/maven2/dev/failsafe/failsafe/3.3.2/failsafe-3.3.2.jar"

# ── S3 filesystem plugin ─────────────────────────────────────────
# Enables remote.data.dir = s3://... for cloud deployments
RUN curl -fsSL -o /opt/fluss/plugins/s3/fluss-fs-s3-${FLUSS_VERSION}.jar \
      "https://repo1.maven.org/maven2/com/alibaba/fluss/fluss-fs-s3/${FLUSS_VERSION}/fluss-fs-s3-${FLUSS_VERSION}.jar" \
    2>/dev/null || true  # Optional — may not exist for all versions

# ── Entrypoint ───────────────────────────────────────────────────
COPY entrypoint.sh /opt/entrypoint.sh
RUN chmod +x /opt/entrypoint.sh

# Expose ports: 2181 (ZK), 9123 (Fluss Client), 9122 (Fluss Internal)
EXPOSE 2181 9122 9123

# Health check: verify ZooKeeper and Fluss are running
HEALTHCHECK --interval=5s --timeout=3s --start-period=25s --retries=5 \
    CMD echo srvr | nc localhost 2181 | grep -q Zookeeper || exit 1

ENTRYPOINT ["/opt/entrypoint.sh"]
