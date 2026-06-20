#!/bin/bash
# ContainerClaw Fluss Cluster Entrypoint
# =======================================
# Starts ZooKeeper, Coordinator, and TabletServer in sequence.
# Designed for single-container deployment (local + cloud).
#
# Environment variables (all optional, sensible defaults):
#   FLUSS_REMOTE_DATA_DIR    - Remote data directory (default: /tmp/fluss/remote-data)
#   FLUSS_ADVERTISED_HOST    - Hostname to advertise (default: localhost)
#   FLUSS_TABLET_SERVER_ID   - TabletServer ID (default: 0)
#   FLUSS_NUM_BUCKETS        - Default bucket count (default: 16)

set -e

# ── Configuration ────────────────────────────────────────────────
REMOTE_DATA_DIR="${FLUSS_REMOTE_DATA_DIR:-/tmp/fluss/remote-data}"
ADVERTISED_HOST="${FLUSS_ADVERTISED_HOST:-localhost}"
TABLET_SERVER_ID="${FLUSS_TABLET_SERVER_ID:-0}"

echo "🚀 [Fluss] Starting unified cluster..."
echo "   Remote data dir: $REMOTE_DATA_DIR"
echo "   Advertised host: $ADVERTISED_HOST"
echo "   TabletServer ID: $TABLET_SERVER_ID"

# ── ZooKeeper ────────────────────────────────────────────────────
echo "📦 [ZooKeeper] Starting..."
mkdir -p /data/zookeeper
echo "1" > /data/zookeeper/myid

cat > /opt/zookeeper/conf/zoo.cfg <<EOF
tickTime=2000
dataDir=/data/zookeeper
clientPort=2181
maxClientCnxns=60
admin.enableServer=false
EOF

/opt/zookeeper/bin/zkServer.sh start
echo "✅ [ZooKeeper] Started on port 2181"

# Wait for ZK to be ready
for i in $(seq 1 30); do
    if echo ruok | nc localhost 2181 2>/dev/null | grep -q imok; then
        break
    fi
    sleep 0.5
done

# ── Fluss Coordinator ────────────────────────────────────────────
echo "📦 [Coordinator] Starting..."

mkdir -p /opt/fluss/conf-coordinator
cat > /opt/fluss/conf-coordinator/server.yaml <<EOF
zookeeper.address: localhost:2181
bind.listeners: INTERNAL://0.0.0.0:9122, CLIENT://0.0.0.0:9123
advertised.listeners: INTERNAL://${ADVERTISED_HOST}:9122, CLIENT://${ADVERTISED_HOST}:9123
internal.listener.name: INTERNAL
remote.data.dir: ${REMOTE_DATA_DIR}
EOF

FLUSS_CONF_DIR=/opt/fluss/conf-coordinator /opt/fluss/bin/coordinator-server.sh start
echo "✅ [Coordinator] Started on port 9123"

# Wait for coordinator to register with ZK
sleep 3

# ── Fluss TabletServer ───────────────────────────────────────────
echo "📦 [TabletServer] Starting..."

mkdir -p /opt/fluss/conf-tablet
cat > /opt/fluss/conf-tablet/server.yaml <<EOF
zookeeper.address: localhost:2181
bind.listeners: INTERNAL://0.0.0.0:9222, CLIENT://0.0.0.0:9223
advertised.listeners: INTERNAL://${ADVERTISED_HOST}:9222, CLIENT://${ADVERTISED_HOST}:9223
internal.listener.name: INTERNAL
tablet-server.id: ${TABLET_SERVER_ID}
kv.snapshot.interval: 0s
data.dir: /tmp/fluss/data
remote.data.dir: ${REMOTE_DATA_DIR}
EOF

FLUSS_CONF_DIR=/opt/fluss/conf-tablet /opt/fluss/bin/tablet-server.sh start
echo "✅ [TabletServer] Started (ID: $TABLET_SERVER_ID)"

echo "🎉 [Fluss] Cluster ready. ZK:2181, Coordinator:9123, TabletServer:19123"

# ── Keep alive ───────────────────────────────────────────────────
# Tail logs to keep container running and forward output
tail -f /opt/fluss/log/*.log 2>/dev/null || \
    while true; do sleep 60; done
