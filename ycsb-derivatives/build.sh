#!/bin/bash
# Builds the trace-recording YCSB bindings against the derivatives' own cores.
# Needs YCSB-IVS (github.com/dliyanage/YCSB-IVS) and YCSB-TS (github.com/martingrambow/YCSB-TS)
# cloned next to this directory.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"

(cd "$ROOT/YCSB-IVS" && mvn -q -pl core -am package -DskipTests -Dcheckstyle.skip \
  && mvn -q -pl core dependency:build-classpath -Dmdep.outputFile="$ROOT/ycsb-derivatives/ivs/core.classpath")
(cd "$ROOT/YCSB-TS" && mvn -q -pl core -am package -DskipTests -Dcheckstyle.skip \
  -Dmaven.compiler.source=8 -Dmaven.compiler.target=8 \
  && mvn -q -pl core dependency:build-classpath -Dmdep.outputFile="$ROOT/ycsb-derivatives/ts/core.classpath")

rm -rf ivs/classes ts/classes
mkdir -p ivs/classes ts/classes
javac -nowarn --release 8 -cp "$ROOT/YCSB-IVS/core/target/core-0.18.0-SNAPSHOT.jar" \
  -d ivs/classes ivs/src/site/ycsb/db/IVSFileClient.java
javac -nowarn --release 8 -cp "$ROOT/YCSB-TS/core/target/core-0.4.0.jar" \
  -d ts/classes ts/src/com/yahoo/ycsb/db/TSFileClient.java
jar cf ivs/ivs-fileclient.jar -C ivs/classes .
jar cf ts/ts-fileclient.jar -C ts/classes .
