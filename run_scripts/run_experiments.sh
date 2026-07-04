#!/bin/bash

mkdir -p /tmp/logs_fig3
mkdir -p /tmp/logs_fig4

TECTONIC_CLI="/home/cc/Tectonic/target/release/tectonic-cli"
KVBENCH_CLI="/home/cc/KV-WorkloadGenerator/bin/load_gen"
YCSB_DIR="/home/cc/Tectonic/rocksdb-benchmark-harness/vendor/YCSB"
M2="/home/cc/.m2/repository"
# Direct java -cp invocation: matches original paper harness (no Python/Maven wrapper overhead)
YCSB_CP="${YCSB_DIR}/file/conf:${YCSB_DIR}/file/target/file-binding-0.18.0-SNAPSHOT.jar:${M2}/org/apache/htrace/htrace-core4/4.1.0-incubating/htrace-core4-4.1.0-incubating.jar:${M2}/org/hdrhistogram/HdrHistogram/2.1.12/HdrHistogram-2.1.12.jar:${M2}/org/codehaus/jackson/jackson-mapper-asl/1.9.4/jackson-mapper-asl-1.9.4.jar:${M2}/org/codehaus/jackson/jackson-core-asl/1.9.4/jackson-core-asl-1.9.4.jar:${YCSB_DIR}/core/target/core-0.18.0-SNAPSHOT.jar"

# Ensure Tectonic is built
cd /home/cc/Tectonic
cargo build --release
cd /home/cc/Tectonic

# --- FIG 3: YCSB Workloads ---
declare -A ycsb_tectonic_specs=(
    ["A"]="a.spec.json"
    ["B"]="b.spec.json"
    ["C"]="c.spec.json"
    ["D"]="d.spec.json"
    ["E"]="e.spec.json"
    ["F"]="f.spec.json"
)

# entry_size=1050 = 1024B value + 26B key (matches original paper logs)
# --ED 3 = zipfian existing point lookup distribution
# --UD 3 = zipfian update distribution
# -L 0.025 = lambda for key generation distribution
declare -A ycsb_kvbench_args=(
    ["A"]="-I 1000000 -Q 500000 -U 500000 --UD 3 --ED 3 --entry_size 1050 -L 0.025"
    ["B"]="-I 1000000 -Q 950000 -U 50000  --UD 3 --ED 3 --entry_size 1050 -L 0.025"
    ["C"]="-I 1000000 -Q 1000000          --UD 3 --ED 3 --entry_size 1050 -L 0.025"
    ["D"]="-I 1050000 -Q 950000           --UD 3 --ED 3 --entry_size 1050 -L 0.025"
    ["E"]="-I 1050000 -S 950000 -Y 0.0001 --YCSB=1 --ED 3 --entry_size 1050 -L 0.025"
)


echo ">>> Running Fig 3 Experiments (YCSB Workloads A-F)"
for w in A B C D E F; do
    echo ">> Workload $w"
    
    # 1. Tectonic
    echo "Running Tectonic..."
    spec="example-specs/ycsb/${ycsb_tectonic_specs[$w]}"
    /usr/bin/time -v $TECTONIC_CLI generate -w $spec -o /tmp/tectonic_out.txt > /tmp/logs_fig3/Tectonic_${w}.log 2>&1
    rm -f /tmp/tectonic_out.txt
    
    # 2. KVBench (No F)
    if [ "$w" != "F" ]; then
        echo "Running KVBench..."
        args=${ycsb_kvbench_args[$w]}
        /usr/bin/time -v $KVBENCH_CLI $args --OP /tmp/kvbench_out.txt > /tmp/logs_fig3/KVBench_${w}.log 2>&1
        rm -f /tmp/kvbench_out.txt
    fi
    
    # 3. YCSB — direct java -cp (matches original paper harness, no Python/Maven wrapper)
    echo "Running YCSB..."
    w_lower="${w,,}"
    # Load phase (-load): insert 1M records using workloada (all workloads have same key space)
    (cd $YCSB_DIR && /usr/bin/time -v taskset -c 0 java -cp "$YCSB_CP" site.ycsb.Client \
      -db site.ycsb.db.FileClient \
      -P workloads/workloada \
      -p file.output=/tmp/ycsb_out.txt \
      -p recordcount=1000000 \
      -p operationcount=1000000 \
      -load) > /tmp/ycsb_load.log 2>&1
    # Run phase (-t): 1M ops with workload-specific mix (reads/updates/scans per workload)
    (cd $YCSB_DIR && /usr/bin/time -v taskset -c 0 java -cp "$YCSB_CP" site.ycsb.Client \
      -db site.ycsb.db.FileClient \
      -P workloads/workload${w_lower} \
      -p file.output=/tmp/ycsb_out.txt \
      -p recordcount=1000000 \
      -p operationcount=1000000 \
      -t) > /tmp/ycsb_run.log 2>&1
    rm -f /tmp/ycsb_out.txt

    # Combine YCSB load+run times (both phases contribute to total latency)
    cat /tmp/ycsb_load.log /tmp/ycsb_run.log > /tmp/logs_fig3/YCSB_${w}.log
done


# --- FIG 4: KVBench Workloads ---
declare -A kvbench_tectonic_specs=(
    ["I"]="i.spec.json"
    ["II"]="ii.spec.json"
    ["III"]="iii.spec.json"
    ["IV"]="iv.spec.json"
    ["V"]="v.spec.json"
)

# Reverse engineered from tectonic specs to get same op count:
# i: 1M insert load. 800k insert, 200k query.
# ii: 100k insert load. 400k query, 100k insert, 150k update, 250k range.
# iii: 1M insert load. 500k query, 250k insert, 250k update.
# iv: 1M insert load. 500k query, 500k range.
# v: 950k insert load. 50k range.
declare -A kvbench_kvbench_args=(
    ["I"]="-I 1000000 -I 800000 -Q 200000"
    ["II"]="-I 100000 -Q 400000 -I 100000 -U 150000 -S 250000"
    ["III"]="-I 1000000 -Q 500000 -I 250000 -U 250000"
    ["IV"]="-I 1000000 -Q 500000 -S 500000"
    ["V"]="-I 950000 -S 50000"
)

echo ">>> Running Fig 4 Experiments (KVBench Workloads I-V)"
for w in I II III IV V; do
    echo ">> Workload $w"
    
    # 1. Tectonic
    echo "Running Tectonic..."
    spec="example-specs/kvbench/${kvbench_tectonic_specs[$w]}"
    /usr/bin/time -v $TECTONIC_CLI generate -w $spec -o /tmp/tectonic_out.txt > /tmp/logs_fig4/Tectonic_${w}.log 2>&1
    rm -f /tmp/tectonic_out.txt
    
    # 2. KVBench
    echo "Running KVBench..."
    # Note: KVBench doesn't let us pass multiple -I flags. It accumulates.
    # So I: -I 1800000 -Q 200000
    # II: -I 200000 -Q 400000 -U 150000 -S 250000
    # III: -I 1250000 -Q 500000 -U 250000
    # IV: -I 1000000 -Q 500000 -S 500000
    # V: -I 950000 -S 50000
    case $w in
        "I") args="-I 1000000 -Q 1000000 -Z 0.8 --ED 0 --ZD 2 --entry_size 1024" ;;
        "II") args="-I 500000 -D 100000 -U 250000 -Q 150000 -Z 1 --ID 0 --UD 0 --ED 0 --ZD 0 --entry_size 1024" ;;
        "III") args="-I 1000000 -U 500000 -Q 500000 -Z 0.5 --UD 3 --ED 0 --ZD 0 --entry_size 1024" ;;
        "IV") args="-I 1000000 -U 500000 -R 500000 -y 0.000001 --UD 3 --entry_size 1024" ;;
        "V") args="-I 950000 -Q 50000 -Z 0 --ID 3 --ED 0 --ZD 0 --entry_size 1024" ;;
    esac

    /usr/bin/time -v $KVBENCH_CLI $args --OP /tmp/kvbench_out.txt > /tmp/logs_fig4/KVBench_${w}.log 2>&1
    rm -f /tmp/kvbench_out.txt
done

echo ">>> All experiments finished!"
