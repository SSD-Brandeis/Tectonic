<div align="center">
  <img width="77" height="64" alt="tectonic logo: three database cylinders shifting like tectonic plates" src="https://github.com/user-attachments/assets/47851a1a-0b6a-4ad7-b7e0-b0e1fe08c52b" />
  <h1>Tectonic</h1>
</div>

## Building

1. First install the rust nightly tool-chain if you have not already
<https://www.rust-lang.org/tools/install>

2. Then build the release version of the project by running the following commands:

```bash
cargo build --release
./target/release/tectonic
# or
cargo run --release
```

### Enabling Additional Databases

By default, the only included database will be a mock database called printdb. If you would like to include more databases, compile the command with `--features <database>`.
The full command should look something like this

```
cargo build --release --features <database>
./target/release/tectonic
# or
cargo run --release --features <database>
```

Currently available databases are:

- printdb
- rocksdb
- cassandra
Alternatively, you can enable all databases by using the flag `--all-features` when compiling.

## Usage

Tectonic has 3 primary commands that you can use for benchmarking:

1. Generate

```bash
./tectonic-cli generate -w workload.spec.json -o workload_output_path
```

Generate takes in a path to a json workload specification file and optionally an output file path as well. Without an output path, it will default to spec.txt. Generate will output a special text file that you can then feed into the execute command to execute the workload on a database. Use `--help` with the generate command to see additional flags that can be used with generate.

2. Execute

```bash
./tectonic-cli execute -i workload.txt -d rocksdb 
```

Execute takes in a path to a previously generated workload text file (through the generate command) and a database name. It will then run the operations in the file on the given database. You can also pass in a path and a configuration string for each database. The path will usually be a file path for local database (i.e. rocksdb) or an endpoint url for networked databases (i.e. cassandra). The config string is database specific. Please see the section on specific databases (TODO) if you are unsure, or run tectonic and it will give you a helpful error if you are missing something. Use `--help` with the execute command to see additional flags that can be used with execute.

3. Benchmark

```bash
./tectonic-cli benchmark -w workload.spec.json -d rocksdb 
```

Benchmark skips the writing to a workload file and will execute operations on the database directly after they are generated. Benchmark has a combination of flags from both the generate and execute commands, but notably does not have an option to specify an output file, or an input workload.txt file. Use `--help` with the benchmark command to see additional flags that can be used with benchmark.

### Additional Commands

1. Schema

```bash
./tectonic-cli schema 
# or
./tectonic-cli schema > workload_schema.json
```

Schema will print the json schema for tectonic, which can be pasted into a file and used to write the spec files for tectonic. The schema will provide helpful autocomplete recommendations for your given editor if you include it at the top of your spec files like so:

```json
"$schema": "<path_to_schema>.spec.json",
```

2. Help

```bash
./tectonic-cli help
```

This will display a help message, enumerate all the possible commands, and give descriptions for each. You can also use `--help` for each command and subcommand to find out more information about its inputs and flags.

## Spec Files

To generate a workload, you first need to write a spec file for that workload, which can be quite a tedious process. There are several ways to make this process less tedious. We recommend using [TexBench](https://github.com/SSD-Brandeis/TexBench) First off, you can use the schema subcommand to get the json schema for tectonic, which will make working in an editor slightly easier. There are also several [example spec files](./example-specs/) in this repository for existing benchmarks that can be used as is or for inspiration. See [Usage.md](/USAGE.md) for more details.

## Profiling

```bash
cargo flamegraph --unit-test workload_gen -- workload_1m_i
```

````
