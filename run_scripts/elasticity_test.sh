#!/bin/bash
set -e

# Helper function to poll cluster size
wait_for_nodes() {
    EXPECTED=$1
    echo "Waiting for cluster to have $EXPECTED nodes in UN (Up Normal) state..."
    while true; do
        # We use awk and grep to count lines starting with UN
        UN_COUNT=$(docker exec cassandra-node-1 nodetool status | grep "^UN" | wc -l || echo "0")
        if [ "$UN_COUNT" -eq "$EXPECTED" ]; then
            echo "Cluster stabilized with $EXPECTED nodes!"
            break
        fi
        echo "Currently $UN_COUNT nodes are UN. Waiting 10 seconds..."
        sleep 10
    done
}

wait_for_execution_phase() {
    echo "Waiting for Load Phase to finish and Execution Phase to begin (reads occurring)..."
    while true; do
        if [ -f metrics.csv ]; then
            LINES=$(cat metrics.csv | wc -l)
            if [ "$LINES" -ge 15 ]; then
                echo "Execution phase established (15 seconds of reads)."
                break
            fi
        fi
        sleep 5
    done
}

cd /home/cc/Tectonic

# Compile Tectonic
echo "[1] Building Tectonic..."
cargo build --release --features db-layer/scylla

# Setup Docker Network
echo "[2] Creating docker network..."
docker network create cassandra-net || true

# Clean up any previous runs
rm -f metrics.csv
docker rm -f cassandra-node-1 cassandra-node-2 cassandra-node-3 || true

# Start Node 1
echo "[3] Starting Cassandra Node 1..."
docker run --name cassandra-node-1 \
  --network cassandra-net \
  -p 9042:9042 \
  -d cassandra:latest

echo "Waiting for Node 1 to initialize cqlsh..."
until docker exec cassandra-node-1 cqlsh -e "DESCRIBE KEYSPACES" > /dev/null 2>&1; do
    echo "Cassandra not ready yet, waiting 5 seconds..."
    sleep 5
done
echo "Node 1 is ready!"

# Initialize ycsb.usertable
echo "[4] Initializing Schema on Node 1..."
docker exec -i cassandra-node-1 cqlsh <<EOF
CREATE KEYSPACE IF NOT EXISTS ycsb WITH REPLICATION = {'class': 'SimpleStrategy', 'replication_factor': 3};
USE ycsb;
CREATE TABLE IF NOT EXISTS usertable (
    y_id varchar primary key,
    field0 varchar,
    field1 varchar,
    field2 varchar,
    field3 varchar,
    field4 varchar,
    field5 varchar,
    field6 varchar,
    field7 varchar,
    field8 varchar,
    field9 varchar
);
EOF

echo "[5] Starting Tectonic Benchmark (Background)..."
# We scale the benchmark to 0.1x (100k inserts) as a proof of concept.
# 16 threads strikes a balance between high throughput and not overloading the single Docker container.
./target/release/tectonic-cli benchmark \
    --ycsb a \
    -d scylla \
    --database-path 127.0.0.1:9042 \
    -s 0.1 \
    -t 16 \
    --status-interval 1 \
    --csv-log metrics.csv &
TECTONIC_PID=$!

wait_for_execution_phase

echo "Waiting an additional 30 seconds to establish steady-state baseline before scaling..."
sleep 30

echo "[6] Starting Cassandra Node 2..."
docker run --name cassandra-node-2 \
  --network cassandra-net \
  -e CASSANDRA_SEEDS=cassandra-node-1 \
  -d cassandra:latest

echo "Polling nodetool status for Node 2 to join and stabilize..."
wait_for_nodes 2

echo "Node 2 stabilized. Waiting 60 seconds for recovered steady-state baseline..."
sleep 60

echo "[7] Starting Cassandra Node 3..."
docker run --name cassandra-node-3 \
  --network cassandra-net \
  -e CASSANDRA_SEEDS=cassandra-node-1,cassandra-node-2 \
  -d cassandra:latest

echo "Polling nodetool status for Node 3 to join and stabilize..."
wait_for_nodes 3

echo "Node 3 stabilized. Waiting 60 seconds for recovered steady-state baseline..."
sleep 60

echo "Cluster expansion complete! Killing Tectonic early..."
kill $TECTONIC_PID || true

echo "[8] Experiment complete. Metrics saved to metrics.csv."
# Cleanup
docker rm -f cassandra-node-1 cassandra-node-2 cassandra-node-3
docker network rm cassandra-net
