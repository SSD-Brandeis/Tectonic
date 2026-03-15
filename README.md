<div align="center">
  <img width="77" height="64" alt="tectonic logo: three database cylinders shifting like tectonic plates" src="https://github.com/user-attachments/assets/47851a1a-0b6a-4ad7-b7e0-b0e1fe08c52b" />
  <h1>Tectonic</h1>
</div>

## Building

<https://www.rust-lang.org/tools/install>

```bash
cargo build --release
./target/release/tectonic
# or
cargo run --release
```

## Usage

```bash
./tectonic-cli schema > workload_schema.json

./tectonic-cli generate -w workload.spec.json
# or
./tectonic-cli generate -w workload.spec.json -o workload_outputs/
# or
./tectonic-cli generate -w workload_specs/ -o workload_outputs/
# or 
./tectonic-cli execute -i workload.txt -d rocksdb 
# or 
./tectonic-cli execute -i workload.txt -d rocksdb -p /tmp/rocksdb
# or 
./tectonic-cli execute -i workload.txt -d rocksdb -p /tmp/rocksdb -c rocksconf.ini
# or 
./tectonic-cli benchmark -w workload.spec.json -d rocksdb -p /tmp/rocksdb -c rocksconf.ini
```

````bash
Usage: tectonic-cli <COMMAND>

Commands:
  generate   Generate workload(s) from a file or folder of workload specifications
  schema     Prints the JSON schema for IDE integration
  execute    Execute a generated workload on a specific database
  benchmark  Generate and Execute a workload from a file against a specific database
  ycsb       Generate and Execute a Ycsb workload
  kvbench   Generate and Execute a KvBench workload
  help       Print this message or the help of the given subcommand(s)

Options:
  -h, --help     Print help
  -V, --version  Print version
```


```bash
Usage: tectonic-cli generate [OPTIONS] --workload <WORKLOAD_PATH>

Options:
  -w, --workload <WORKLOAD_PATH>  File or folder of workload spec files
  -o, --output <OUTPUT>           Output file or folder for workload(s). Defaults to the same directory as the workload spec
  -h, --help                      Print help
```
```
Execute a generated workload on a specific database

Usage: tectonic-cli execute [OPTIONS] --input-workload <INPUT_FILE> --database <DATABASE>

Options:
  -i, --input-workload <INPUT_FILE>  Tectonic generated workload file
  -d, --database <DATABASE>          Name of the database on which to execute operations
  -p, --database-path <DB_PATH>      Path to the database
  -c, --config <CONFIG>              Configuration string (database dependent)
  -h, --help                         Print help
```

```
Generate and Execute a workload from a file against a specific database

Usage: tectonic-cli benchmark [OPTIONS] --workload <WORKLOAD_PATH> --database <DATABASE>

Options:
  -w, --workload <WORKLOAD_PATH>  File of workload spec files
  -d, --database <DATABASE>       Name of the database on which to execute operations
  -p, --database-path <DB_PATH>   Path to the database
  -c, --config <CONFIG>           Configuration string (database dependent)
  -h, --help                      Print help
```

```
Generate and Execute a Ycsb workload

Usage: tectonic-cli ycsb [OPTIONS] --name <WORKLOAD_NAME> --database <DATABASE>

Options:
  -w, --name <WORKLOAD_NAME>     Name of ycsb workload (a-f)
  -s, --scale <SCALE>            Scale factor for the ycsb workload
  -d, --database <DATABASE>      Name of the database on which to execute operations
  -p, --database-path <DB_PATH>  Path to the database
  -c, --config <CONFIG>          Configuration string (database dependent)
  -h, --help                     Print help
```

```
Generate and Execute a KvBench workload

Usage: tectonic-cli kvbench [OPTIONS] --name <WORKLOAD_NAME> --database <DATABASE>

Options:
  -w, --name <WORKLOAD_NAME>     Name of ycsb workload (i-v)
  -d, --database <DATABASE>      Name of the database on which to execute operations
  -p, --database-path <DB_PATH>  Path to the database
  -c, --config <CONFIG>          Configuration string (database dependent)
  -h, --help                     Print help

```


## Profiling

```bash
cargo flamegraph --unit-test workload_gen -- workload_1m_i
```
````
