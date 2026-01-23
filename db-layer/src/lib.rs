#![allow(clippy::needless_return)]
#![feature(duration_millis_float)]

use anyhow::{Result, anyhow, bail};
use std::collections::HashMap;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::time::{self};

mod printdb;
use printdb::PrintDB;
mod rocksdb;
use rocksdb::RocksDB;

// fn main() -> Result<()> {
//     // Ask for an input file (workflow file) and database layer
//     // Init database layer
//     // Read through the workflow file line by line and call operations from intialized database
//     // layer
//     //
//     //
//     // TODO: Keep track of:
//     // average operation latency and for each operation
//     // Total Throughout
//     // Successful operations (think about point queries)
//     // Number of operations for each operation and total
//     // Min latency
//     // Max latency
//     // 50th percentile latency for operations
//     // 95th percentile latency for operations
//     // 99th percentile latency for operations
//     //
//     // TODO: Windowed version of operations for printing status?
//     //
//     // Easiest way to do this is a hashmap
//
//     let args: Vec<String> = std::env::args().collect();
//     if args.len() != 3 {
//         bail!("Format: {} <db_name> <workload_file>", { args[0].clone() });
//     }
//
//     let file_path = args[2].clone();
//
//     let db_layer: Box<dyn DBTranslationLayer> = match &*args[1].clone().to_ascii_lowercase() {
//         "printdb" => match PrintDB::new() {
//             Ok(db) => Box::new(db),
//             Err(err) => {
//                 bail!("Failed to create db because of error {err}");
//             }
//         },
//         "rocksdb" => match RocksDB::new() {
//             Ok(db) => Box::new(db),
//             Err(err) => {
//                 bail!("Failed to create db because of error {err}");
//             }
//         },
//         _ => panic!("Unsupported database. Supported databases are printdb and rocksdb"),
//     };
//     benchmark_db(db_layer.as_ref(), file_path)?;
//     return Ok(());
// }

// FIX: Can have this function call the benchmark function associated instead
pub fn invoke_benchmark(name: &str, input_file: String) -> Result<()> {
    match name {
        "printdb" => benchmark_db(PrintDB::new()?, input_file)?,
        // Err(err) => {
        //     bail!("Failed to create db because of error {err}");
        // }
        "rocksdb" => benchmark_db(RocksDB::new()?, input_file)?,
        // Err(err) => {
        //     bail!("Failed to create db because of error {err}");
        // }
        _ => bail!("Unsupported database. Supported databases are printdb and rocksdb"),
    };

    Ok(())
}

pub fn benchmark_db<DB: DBTranslationLayer>(db_layer: DB, input_file: String) -> Result<()> {
    let file = File::open(input_file)?;
    let buf_reader = BufReader::new(file);

    let mut operation_statistics = HashMap::<&str, Statistics>::new();

    let start_time = std::time::Instant::now();
    for line in buf_reader.lines() {
        process_line(line, &db_layer, &mut operation_statistics)?;
    }

    let end_time = std::time::Instant::now();

    let mut total_operation_counts = 0;
    // Print out statistics
    for (operation, stats) in operation_statistics {
        let operation = match operation {
            "I" => "Insert",
            "P" => "Point Query",
            "U" => "Update",
            "S" => "Range Query",
            "D" => "Delete",
            "M" => "Merge",
            "R" => "Range Delete",
            _ => panic!("Unknown operation in statistics set"),
        };
        total_operation_counts += stats.count;
        println!("[{}] Count: {}", operation, stats.count);
        println!("[{}] Total Latency: {}ms", operation, stats.sum);
        println!(
            "[{}] Average Latency: {}ms",
            operation,
            stats.sum / stats.count as f64
        );
        if let Some(min) = stats.min {
            println!("[{}] Minimum Latency: {}ms", operation, min);
        }
        if let Some(max) = stats.max {
            println!("[{}] Maximum Latency: {}ms", operation, max);
        }
    }
    println!(
        "[Overall] Throughput (ops/ms): {}",
        total_operation_counts as f64 / end_time.duration_since(start_time).as_millis_f64()
    );

    return Ok(());
}

#[derive(Default)]
struct Statistics {
    operation_latencies: Vec<f64>,
    count: usize,
    sum: f64,
    min: Option<f64>,
    max: Option<f64>,
}

impl Statistics {
    fn add_latency(&mut self, latency: f64) {
        self.count += 1;
        self.sum += latency;
        match &mut self.min {
            Some(min) => {
                if *min > latency {
                    *min = latency;
                }
            }
            None => self.min = Some(latency),
        }

        match &mut self.max {
            Some(max) => {
                if *max > latency {
                    *max = latency;
                }
            }
            None => self.max = Some(latency),
        }

        self.operation_latencies.push(latency);
    }
}

fn process_line<DB: DBTranslationLayer>(
    line: Result<String, std::io::Error>,
    db_layer: &DB,
    operation_statistics_map: &mut HashMap<&str, Statistics>,
) -> Result<()> {
    let line = line?;
    let mut line_iter = line.split_whitespace();
    let operation = line_iter.next().unwrap();

    let start_time: std::time::Instant;
    let op_key: &str;

    match operation {
        "I" => {
            let key = line_iter
                .next()
                .ok_or(anyhow!("Missing Argument"))?
                .to_string();
            let value = line_iter
                .next()
                .ok_or(anyhow!("Missing Argument"))?
                .to_string();

            op_key = "I";
            start_time = std::time::Instant::now();
            db_layer.insert(key, value)?;
        }
        "P" => {
            let key = line_iter
                .next()
                .ok_or(anyhow!("Missing Argument"))?
                .to_string();
            op_key = "P";
            start_time = std::time::Instant::now();
            db_layer.point_query(key)?;
        }
        "U" => {
            let key = line_iter
                .next()
                .ok_or(anyhow!("Missing Argument"))?
                .to_string();
            let value = line_iter
                .next()
                .ok_or(anyhow!("Missing Argument"))?
                .to_string();
            op_key = "U";
            start_time = std::time::Instant::now();
            db_layer.update(key, value)?;
        }
        "M" => {
            let key = line_iter
                .next()
                .ok_or(anyhow!("Missing Argument"))?
                .to_string();
            let value = line_iter
                .next()
                .ok_or(anyhow!("Missing Argument"))?
                .to_string();

            op_key = "M";
            start_time = time::Instant::now();

            db_layer.merge(key, value)?;
        }
        "D" => {
            let key = line_iter
                .next()
                .ok_or(anyhow!("Missing Argument"))?
                .to_string();
            op_key = "D";
            start_time = std::time::Instant::now();
            db_layer.point_delete(key)?;
        }
        "S" => {
            let start_key = line_iter
                .next()
                .ok_or(anyhow!("Missing Argument"))?
                .to_string();
            let bound = line_iter.next().ok_or(anyhow!("Missing Argument"))?;

            op_key = "S";
            start_time = time::Instant::now();
            if let Ok(range) = bound.parse::<usize>() {
                db_layer.range_query_count(start_key, range)?;
            } else {
                db_layer.range_query(start_key, bound.to_string())?;
            }
        }
        "R" => {
            // Range delete
            let start_key = line_iter
                .next()
                .ok_or(anyhow!("Missing Argument"))?
                .to_string();
            let bound = line_iter.next().ok_or(anyhow!("Missing Argument"))?;

            op_key = "R";
            start_time = time::Instant::now();

            if let Ok(range) = bound.parse::<usize>() {
                db_layer.range_delete_count(start_key, range)?;
            } else {
                db_layer.range_delete(start_key, bound.to_string())?;
            }
        }
        _ => bail!("Unknown operation \"{}\"", operation),
    };

    let latency = std::time::Instant::now()
        .duration_since(start_time)
        .as_millis_f64();
    let map = operation_statistics_map.entry(op_key).or_default();
    map.add_latency(latency);

    Ok(())
}

pub trait DBTranslationLayer {
    // Setup
    fn init(&mut self) -> Result<()>;
    fn cleanup(self) -> Result<()>;

    // Operations
    fn insert(&self, key: String, value: String) -> Result<()>;
    fn update(&self, key: String, value: String) -> Result<()>;
    fn merge(&self, key: String, value: String) -> Result<()>;
    fn point_delete(&self, key: String) -> Result<()>;
    fn point_query(&self, key: String) -> Result<()>;
    fn range_query(&self, start_key: String, end_key: String) -> Result<()>;
    fn range_query_count(&self, start_key: String, range: usize) -> Result<()>;
    fn range_delete(&self, start_key: String, end_key: String) -> Result<()>;
    fn range_delete_count(&self, start_key: String, range: usize) -> Result<()>;
}
