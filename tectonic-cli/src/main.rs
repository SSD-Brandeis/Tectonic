#![allow(clippy::needless_return)]
use anyhow::{Context, Result, bail};
use clap::{Parser, Subcommand};
use db_layer::{benchmark_db, get_database_layer};
use rayon::iter::ParallelIterator;
use rayon::prelude::ParallelBridge;
use std::{
    fs,
    path::{Path, PathBuf},
};
use tectonic::{generate_workload, generate_workload_spec_schema};
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
        /// File or folder of workload spec files
        #[arg(short = 'w', long = "workload")]
        workload_path: String,

        /// Output file or folder for workload(s). Defaults to the same directory as the workload spec.
        #[arg(short = 'o', long = "output", required = false)]
        output: Option<String>,
    },
    /// Prints the JSON schema for IDE integration.
    Schema,
    Benchmark {
        #[arg(short = 'i', long = "input-workload")]
        input_file: String,
        #[arg(short = 'd', long = "database")]
        database: String,
    },
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
        } => invoke_generate(&workload_path, output.as_deref()),
        Command::Schema => invoke_schema(),
        Command::Benchmark {
            input_file,
            database,
        } => {
            let db_layer = get_database_layer(&database)?;
            benchmark_db(db_layer.as_ref(), input_file)
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
fn invoke_generate(workload_path: &str, output: Option<&str>) -> Result<()> {
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

                return generate_workload(&contents, &output_file_path);
            })
            .collect::<Result<Vec<_>>>()?;
    } else if workload_path.is_file() {
        let output_file = output
            .map(PathBuf::from)
            .unwrap_or_else(|| spec_path_to_workload_name(&workload_path).into());

        let contents = fs::read_to_string(&workload_path)?;

        generate_workload(&contents, &output_file)?;
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
    Ok(())
}
