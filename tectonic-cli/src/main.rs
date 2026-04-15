#![allow(clippy::needless_return)]
use anyhow::{Context, Result, bail};
use clap::{Args, Parser, Subcommand};
use db_layer::execute_operations;
use rayon::iter::ParallelIterator;
use rayon::prelude::ParallelBridge;
use std::{
    fs,
    path::{Path, PathBuf},
};
use tectonic::{
    benchmark_workload, generate_workload, generate_workload_spec_schema,
    scale_and_benchmark_workload, scale_and_generate_workload,
};
use tracing::info;
use tracing_subscriber::EnvFilter;
use walkdir::WalkDir;

#[derive(Parser, Debug)]
#[command(version, about, long_about = None)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Generate workload(s) from a file or folder of workload specifications.
    Generate {
        #[command(flatten)]
        workload_path: WorkloadPath,

        /// Output file or folder for workload(s). Defaults to the same directory as the workload spec.
        #[arg(short = 'o', long = "output", required = false)]
        output: Option<String>,

        /// Scale factor for the operation counts of the workload
        #[arg(short = 's', long = "scale")]
        scale: Option<f64>,
    },
    /// Prints the JSON schema for IDE integration.
    Schema,
    /// Execute a generated workload on a specific database
    Execute {
        /// Tectonic generated workload file
        #[arg(short = 'i', long = "input-workload")]
        generated_workload: String,
        /// Name of the database on which to execute operations
        #[arg(short = 'd', long = "database")]
        database: String,
        /// Path to the database
        #[arg(short = 'p', long = "database-path")]
        db_path: Option<String>,
        /// Configuration string (database dependent)
        #[arg(short = 'c', long = "config")]
        config: Option<String>,
    },
    /// Generate and Execute a workload from a spec file against a specific database
    Benchmark {
        #[command(flatten)]
        workload_path: WorkloadPath,
        /// Name of the database on which to execute operations
        #[arg(short = 'd', long = "database")]
        database: String,
        /// Path to the database
        #[arg(short = 'p', long = "database-path")]
        db_path: Option<String>,
        /// Configuration string (database dependent)
        #[arg(short = 'c', long = "config")]
        config: Option<String>,

        /// Scale factor for the operation counts of the workload
        #[arg(short = 's', long = "scale")]
        scale: Option<f64>,
    },
    ///// Generate and Execute a Ycsb workload
    //Ycsb {
    //    /// Name of ycsb workload (a-f)
    //    #[arg(short = 'w', long = "name")]
    //    workload_name: String,
    //    /// Scale factor for the ycsb workload
    //    #[arg(short = 's', long = "scale")]
    //    scale: Option<f64>,
    //    /// Name of the database on which to execute operations
    //    #[arg(short = 'd', long = "database")]
    //    database: String,
    //    /// Path to the database
    //    #[arg(short = 'p', long = "database-path")]
    //    db_path: Option<String>,
    //    /// Configuration string (database dependent)
    //    #[arg(short = 'c', long = "config")]
    //    config: Option<String>,
    //},
    ///// Generate and Execute a KvBench workload
    //Kvbench {
    //    /// Name of ycsb workload (i-v)
    //    #[arg(short = 'w', long = "name")]
    //    workload_name: String,
    //    /// Name of the database on which to execute operations
    //    #[arg(short = 'd', long = "database")]
    //    database: String,
    //    /// Path to the database
    //    #[arg(short = 'p', long = "database-path")]
    //    db_path: Option<String>,
    //    /// Configuration string (database dependent)
    //    #[arg(short = 'c', long = "config")]
    //    config: Option<String>,
    //},
}

#[derive(Debug, Args)]
#[group(required = true, multiple = false)]
struct WorkloadPath {
    /// File or folder of workload spec files
    #[arg(short = 'w', long = "workload")]
    workload_path: Option<String>,
    /// Name of the ycsb workload (ex: a, b, c ...)
    #[arg(long = "ycsb")]
    ycsb_workload: Option<String>,
    /// Name of the kvbench workload (ex: i or 1, ii or 2, etc...)
    #[arg(long = "kvbench")]
    kvbench_workload: Option<String>,
    #[arg(long = "db_bench")]
    db_bench_workload: Option<String>,
}

impl WorkloadPath {
    fn into_path(self) -> Result<String> {
        if let Some(path) = self.workload_path {
            return Ok(path);
        } else if let Some(ycsb_name) = self.ycsb_workload {
            let ycsb_name = match ycsb_name.to_lowercase().as_str() {
                "a" | "workloada" => "a",
                "b" | "workloadb" => "b",
                "c" | "workloadc" => "c",
                "d" | "workloadd" => "d",
                "e" | "workloade" => "e",
                "f" | "workloadf" => "f",
                _ => bail!("Unknown YCSB workload: {:?}", ycsb_name),
            };

            return Ok(format!(
                "{}/../example-specs/ycsb/{}.spec.json",
                env!("CARGO_MANIFEST_DIR"),
                ycsb_name
            ));
        } else if let Some(kvbench_name) = self.kvbench_workload {
            let kvbench_name = match kvbench_name.to_lowercase().as_str() {
                "1" | "i" => "i",
                "2" | "ii" => "ii",
                "3" | "iii" => "iii",
                "4" | "iv" => "iv",
                "5" | "v" => "v",
                _ => bail!("Unknown KVBench workload: {:?}", kvbench_name),
            };
            return Ok(format!(
                "{}/../example-specs/kvbench/{}.spec.json",
                env!("CARGO_MANIFEST_DIR"),
                kvbench_name
            ));
        } else if let Some(db_bench_name) = self.db_bench_workload {
            todo!()
        } else {
            unreachable!()
        }
    }
}

fn main() -> Result<()> {
    let args = Cli::parse();
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env())
        .init();

    match args.command {
        Command::Generate {
            workload_path,
            output,
            scale,
        } => {
            let workload_path = workload_path.into_path()?;

            if let Some(scale) = scale
                && scale != 1.0
            {
                return invoke_generate(
                    &workload_path,
                    output.as_deref(),
                    |workload_spec_string, output_file_path| {
                        scale_and_generate_workload(workload_spec_string, output_file_path, scale)
                    },
                );
            } else {
                return invoke_generate(
                    &workload_path,
                    output.as_deref(),
                    |workload_spec_string, output_file_path| {
                        generate_workload(workload_spec_string, output_file_path)
                    },
                );
            }
        }
        Command::Schema => return invoke_schema(),
        Command::Execute {
            input_workload_file_path: input_file,
            database,
            db_path,
            config,
        } => {
            return execute_operations(
                &database,
                input_file,
                db_path.as_deref(),
                config.as_deref(),
            );
        }
        Command::Benchmark {
            workload_path,
            database,
            db_path,
            config,
            scale,
        } => {
            let workload_path = workload_path.into_path()?;

            if let Some(scale) = scale
                && scale != 1.0
            {
                return invoke_benchmark(
                    &workload_path,
                    &database,
                    |workload_spec_string, database_name| {
                        scale_and_benchmark_workload(
                            workload_spec_string,
                            database_name,
                            db_path.as_deref(),
                            config.as_deref(),
                            scale,
                        )
                    },
                );
            } else {
                return invoke_benchmark(
                    &workload_path,
                    &database,
                    |workload_spec_string, database_name| {
                        benchmark_workload(
                            workload_spec_string,
                            database_name,
                            db_path.as_deref(),
                            config.as_deref(),
                        )
                    },
                );
            }
        }
    }
}

fn spec_path_to_workload_name(spec_path: impl AsRef<Path>) -> String {
    fn spec_path_to_workload_name_inner(spec_path: &Path) -> String {
        return spec_path
            .file_name()
            .and_then(|stem| stem.to_str())
            .map(|stem| {
                let temp = stem.rsplitn(3, '.').collect::<Vec<_>>();
                println!("{:#?}", temp);
                temp[1]
            }) // file.spec.json -> file
            .map(|stem| format!("{stem}.txt")) // file -> file.txt
            .unwrap_or_else(|| {
                let filename = spec_path.file_name().unwrap().to_string_lossy();
                let basename = filename
                    .rsplit_once('.')
                    .map_or(filename.as_ref(), |(base, _)| base);
                format!("{basename}.txt")
            });
    }

    return spec_path_to_workload_name_inner(spec_path.as_ref());
}

/// Generate workload(s) from a file or folder of workload specifications.
fn invoke_generate(
    workload_path: &str,
    output: Option<&str>,
    generate_func: impl Fn(String, &PathBuf) -> Result<()> + Send + Sync,
) -> Result<()> {
    let workload_path = PathBuf::from(workload_path);
    if !workload_path.exists() {
        bail!("File or folder does not exist {}", workload_path.display());
    }

    if workload_path.is_dir() {
        let output_dir = output
            .map(PathBuf::from)
            .unwrap_or_else(|| workload_path.clone());
        if !output_dir.exists() {
            fs::create_dir_all(&output_dir)?;
        }

        WalkDir::new(&workload_path)
            .follow_links(true)
            .into_iter()
            .filter_map(Result::ok)
            .filter(|file| {
                file.file_type().is_file()
                    && file
                        .path()
                        .file_name()
                        .and_then(|name| name.to_str())
                        .map(
                            |name| name.ends_with(".spec.json"), // || name.ends_with(".spec.jsonc")
                        )
                        .unwrap_or(false)
            })
            .par_bridge()
            .map(|entry| -> Result<_> {
                let path = entry.path();
                info!("Generating workload for: {}", path.display());
                let contents = fs::read_to_string(path)?;

                let output_file = spec_path_to_workload_name(path);

                let mut output_file_path = output_dir.clone();
                output_file_path.push(output_file);

                return generate_func(contents, &output_file_path);
            })
            .collect::<Result<Vec<_>>>()?;
    } else if workload_path.is_file() {
        let output_file = output
            .map(PathBuf::from)
            .unwrap_or_else(|| spec_path_to_workload_name(&workload_path).into());

        let contents = fs::read_to_string(&workload_path)?;

        generate_func(contents, &output_file)?;
    } else {
        unreachable!("Path is neither a file nor a directory");
    };

    return Ok(());
}

/// Generate workload(s) from a file or folder of workload specifications.
fn invoke_benchmark(
    workload_path: &str,
    database_name: &str,
    benchmark_func: impl Fn(String, &str) -> Result<()>,
) -> Result<()> {
    let workload_path = PathBuf::from(workload_path);
    if !workload_path.exists() {
        bail!("File or folder does not exist {}", workload_path.display());
    }

    if workload_path.is_dir() {
        bail!("Cannot benchmark a directory");
    } else if workload_path.is_file() {
        let contents = fs::read_to_string(&workload_path)?;

        benchmark_func(contents, database_name)?;
    } else {
        unreachable!("Path is neither a file nor a directory");
    };

    return Ok(());
}

/// Prints the json schema for IDE integration.
fn invoke_schema() -> Result<()> {
    let schema_str = generate_workload_spec_schema().context("Schema generation failed.")?;
    println!("{schema_str}");

    return Ok(());
}
