# YCSB-TS in X-Bench

`energy.spec.json` reproduces `workloads/energy` of [YCSB-TS](https://github.com/martingrambow/YCSB-TS)
(TSDBBench), unmodified, at its original record and operation counts. It is the YCSB-TS workload used in
the R1.O7 comparison.

A data point is one key-value pair in the row-key layout of key-value backed TSDBs such as OpenTSDB
(the layout of the recording binding `TSFileClient`):

```
key   usermetric:<13-digit timestamp in ms>:Region0=<tag value>
value <measurement>
```

## How YCSB-TS's energy workload maps to the spec

| YCSB-TS (`workloads/energy`) | X-Bench spec |
| --- | --- |
| load: `recordcount=2190000` inserts; `randomfloorfilling=true` fills the time domain with consecutive timestamps from `insertstart` (2019-01-01) | group `load`: 2,190,000 `inserts`; timestamp `counter` starting at `insertstart` |
| run: `operationcount=2268840`, ops drawn at random with weights insert 2123, scan 34, avg 34, count 0, sum 8 | group `run`: the expected counts, 2,190,426 `inserts` and 78,414 `blind_range_queries` |
| run inserts: timestamp uniform in `[insertstart, insertend)` (2019) | `number` with a `uniform` value over the same range |
| tag value: one of `predefinedtagvalueamount=5` random 4-character strings, a new set in each phase (load, run) | `hot_range` with `len 4`, `amount 5`, `probability 1`: 5 random strings, picked uniformly. Load and run have their own `hot_range`, so each gets its own set |
| measurement: a random double written by `Double.toString` (11–21 characters, mean 17.3) | a random numeric string of 17 characters (the mean length, rounded) |
| scan, avg, count, sum: all read the points of a time window. Its length is uniform in `[minscanlength, maxscanlength]` (1–10 days), it starts at a uniform time and is redrawn until it ends by `insertend` | `blind_range_queries` from `usermetric:<uniform timestamp>`, `span` uniform in [1, 10] days on the timestamp, `span_max_end` = `insertend` |

avg, count and sum differ from scan only in what is computed from the returned points, so all four are one
range query. YCSB-TS has no updates, deletes or point reads in this workload (`readproportion=0`).

Nothing in the spec is copied from a YCSB-TS run: every count, range and length comes from
`workloads/energy`. The only exception is the measurement length (17), the mean of the values YCSB-TS
writes. The tag values are generated at random, as YCSB-TS does.

## Generate

```bash
tectonic-cli generate -w example-specs/ycsb-ts/energy.spec.json -o energy.txt
```

The spec is written by `run_scripts/run_ycsb_derivatives_fidelity.py` (`ts_spec`), which also runs the
comparison. Results: `data/revision/R1.O7_ycsb-derivatives/`.
