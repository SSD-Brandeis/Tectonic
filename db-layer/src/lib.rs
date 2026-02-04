#![allow(clippy::needless_return)]
#![feature(duration_millis_float)]

use anyhow::{Result, anyhow, bail};
use enum_dispatch::enum_dispatch;
use std::collections::HashMap;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::time::{self};

mod printdb;
use printdb::PrintDB;
mod rocksdb;
use rocksdb::RocksDB;

pub type Key = [u8];
pub type Value = [u8];

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
                if *max < latency {
                    *max = latency;
                }
            }
            None => self.max = Some(latency),
        }

        self.operation_latencies.push(latency);
    }
}
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

pub fn benchmark_db(db_layer: Db, input_file: String) -> Result<()> {
    let file = File::open(input_file)?;
    let mut buf_reader = BufReader::new(file);

    let mut benchmarker = Benchmarker::new(db_layer);

    benchmarker.start();
    // for line in buf_reader.lines() {
    //     process_line(&line?, &mut benchmarker)?;
    // }

    let mut buf = String::new();
    while buf_reader.read_line(&mut buf)? > 0 {
        if buf.ends_with('\n') {
            buf.pop();
            if buf.ends_with('\r') {
                buf.pop();
            }
        }

        process_line(&buf, &mut benchmarker)?;
    }

    benchmarker.end();

    // Print out statistics
    benchmarker.print_summary();

    return Ok(());
}

fn process_line(line: &str, benchmarker: &mut Benchmarker) -> Result<()> {
    let mut line_iter = line.split_whitespace();
    let operation = match line_iter.next() {
        Some(op) => op,
        None => return Ok(()),
    };

    match operation {
        "I" => {
            let key = line_iter
                .next()
                .ok_or(anyhow!("Missing Argument"))?
                .as_bytes();
            let value = line_iter
                .next()
                .ok_or(anyhow!("Missing Argument"))?
                .as_bytes();

            benchmarker.handle_insert(key, value)?;
        }
        "P" => {
            let key = line_iter
                .next()
                .ok_or(anyhow!("Missing Argument"))?
                .as_bytes();
            benchmarker.handle_point_query(key)?;
        }
        "U" => {
            let key = line_iter
                .next()
                .ok_or(anyhow!("Missing Argument"))?
                .as_bytes();
            let value = line_iter
                .next()
                .ok_or(anyhow!("Missing Argument"))?
                .as_bytes();
            benchmarker.handle_update(key, value)?;
        }
        "M" => {
            let key = line_iter
                .next()
                .ok_or(anyhow!("Missing Argument"))?
                .as_bytes();
            let value = line_iter
                .next()
                .ok_or(anyhow!("Missing Argument"))?
                .as_bytes();

            benchmarker.handle_merge(key, value)?;
        }
        "D" => {
            let key = line_iter
                .next()
                .ok_or(anyhow!("Missing Argument"))?
                .as_bytes();
            benchmarker.handle_point_delete(key)?;
        }
        "S" => {
            let start_key = line_iter
                .next()
                .ok_or(anyhow!("Missing Argument"))?
                .as_bytes();
            let bound = line_iter.next().ok_or(anyhow!("Missing Argument"))?;

            if let Ok(range) = bound.parse::<usize>() {
                benchmarker.handle_range_query_count(start_key, range)?;
            } else {
                benchmarker.handle_range_query(start_key, bound.as_bytes())?;
            }
        }
        "R" => {
            // Range delete
            let start_key = line_iter
                .next()
                .ok_or(anyhow!("Missing Argument"))?
                .as_bytes();
            let bound = line_iter.next().ok_or(anyhow!("Missing Argument"))?;

            if let Ok(range) = bound.parse::<usize>() {
                benchmarker.handle_range_delete_count(start_key, range)?;
            } else {
                benchmarker.handle_range_delete(start_key, bound.as_bytes())?;
            }
        }
        _ => bail!("Unknown operation \"{}\"", operation),
    };

    Ok(())
}

pub struct Benchmarker<'a> {
    db_layer: Db,
    operation_statistics_map: HashMap<&'a str, Statistics>,
    start_time: Option<time::Instant>,
    end_time: Option<time::Instant>,
}

macro_rules! measure {
    ($self:ident, $op_key:expr, $call:expr) => {
        let start_time = time::Instant::now();

        $call;

        let latency = std::time::Instant::now()
            .duration_since(start_time)
            .as_millis_f64();
        let map = $self.operation_statistics_map.entry($op_key).or_default();
        map.add_latency(latency);
    };
}

impl<'a> Benchmarker<'a> {
    pub fn new(db_layer: Db) -> Self {
        Self {
            db_layer,
            operation_statistics_map: HashMap::new(),
            start_time: None,
            end_time: None,
        }
    }

    pub fn start(&mut self) {
        self.start_time = Some(time::Instant::now());
    }

    pub fn end(&mut self) {
        self.end_time = Some(time::Instant::now());
    }

    pub fn print_summary(&self) {
        let mut total_operation_counts = 0;
        let mut total_operation_timing_sum = 0.0;
        // Print out statistics
        for (&operation, stats) in self.operation_statistics_map.iter() {
            total_operation_timing_sum += stats.sum;
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
                "[{}] Average Latency: {}us",
                operation,
                stats.sum / stats.count as f64 * 1000.0
            );
            if let Some(min) = stats.min {
                println!("[{}] Minimum Latency: {}us", operation, min * 1000.0);
            }
            if let Some(max) = stats.max {
                println!("[{}] Maximum Latency: {}us", operation, max * 1000.0);
            }
        }

        if let (Some(start_time), Some(end_time)) = (self.start_time, self.end_time) {
            println!(
                "[Overall] Throughput (using start and end time) (ops/ms): {}",
                total_operation_counts as f64 / end_time.duration_since(start_time).as_millis_f64()
            );
        }
        println!(
            "[Overall] Throughput (using aggregate operation times) (ops/ms): {}",
            total_operation_counts as f64 / total_operation_timing_sum
        );
    }

    pub fn handle_insert(&mut self, key: &Key, value: &Value) -> Result<()> {
        // Generate value from string expression
        // Either insert value into database or write to file based on match
        measure!(self, "I", self.db_layer.insert(key, value)?);

        return Ok(());
    }

    pub fn handle_update(&mut self, key: &Key, value: &Value) -> Result<()> {
        measure!(self, "U", self.db_layer.update(key, value)?);

        return Ok(());
    }

    pub fn handle_merge(&mut self, key: &Key, value: &Value) -> Result<()> {
        measure!(self, "M", self.db_layer.merge(key, value)?);

        return Ok(());
    }

    pub fn handle_point_delete(&mut self, key: &Key) -> Result<()> {
        measure!(self, "D", self.db_layer.point_delete(key)?);

        return Ok(());
    }

    pub fn handle_point_query(&mut self, key: &Key) -> Result<()> {
        measure!(self, "P", self.db_layer.point_query(key)?);

        return Ok(());
    }

    pub fn handle_range_query(&mut self, key1: &Key, key2: &Key) -> Result<()> {
        measure!(self, "S", self.db_layer.range_query(key1, key2)?);

        return Ok(());
    }

    pub fn handle_range_query_count(&mut self, key1: &Key, count: usize) -> Result<()> {
        measure!(self, "S", self.db_layer.range_query_count(key1, count)?);
        return Ok(());
    }

    pub fn handle_range_delete(&mut self, start_key: &Key, end_key: &Key) -> Result<()> {
        measure!(self, "R", self.db_layer.range_delete(start_key, end_key)?);

        return Ok(());
    }

    pub fn handle_range_delete_count(&mut self, start_key: &Key, count: usize) -> Result<()> {
        measure!(
            self,
            "R",
            self.db_layer.range_delete_count(start_key, count)?
        );

        return Ok(());
    }
}

#[enum_dispatch]
pub trait DBTranslationLayer {
    // Setup
    fn init(&mut self) -> Result<()>;
    fn cleanup(self) -> Result<()>;

    // Operations
    fn insert(&self, key: &Key, value: &Value) -> Result<()>;
    fn update(&self, key: &Key, value: &Value) -> Result<()>;
    fn merge(&self, key: &Key, value: &Value) -> Result<()>;
    fn point_delete(&self, key: &Key) -> Result<()>;
    fn point_query(&self, key: &Key) -> Result<()>;
    fn range_query(&self, start_key: &Key, end_key: &Value) -> Result<()>;
    fn range_query_count(&self, start_key: &Key, range: usize) -> Result<()>;
    fn range_delete(&self, start_key: &Key, end_key: &Key) -> Result<()>;
    fn range_delete_count(&self, start_key: &Key, range: usize) -> Result<()>;
}

#[enum_dispatch(DBTranslationLayer)]
pub enum Db {
    PrintDB,
    RocksDB,
}

impl Db {
    pub fn new(database_name: &str) -> Result<Self> {
        Ok(match database_name {
            "printdb" => Self::PrintDB(PrintDB::new()?),
            // Err(err) => {
            //     bail!("Failed to create db because of error {err}");
            // }
            "rocksdb" => Self::RocksDB(RocksDB::new()?),
            // Err(err) => {
            //     bail!("Failed to create db because of error {err}");
            // }
            _ => bail!("Unsupported database. Supported databases are printdb and rocksdb"),
        })
    }
}

pub fn execute_operations(name: &str, input_file: String) -> Result<()> {
    benchmark_db(Db::new(name)?, input_file)
}
