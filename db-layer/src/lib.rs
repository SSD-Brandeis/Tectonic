#![allow(clippy::needless_return)]
#![feature(duration_millis_float, trait_alias)]

use anyhow::{Context, Result, anyhow, bail};
use enum_dispatch::enum_dispatch;
use hdrhistogram::{Counter, Histogram};
use indicatif::{ProgressBar, ProgressDrawTarget, ProgressStyle};
use std::collections::HashMap;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::num::TryFromIntError;
use std::ops::AddAssign;
use std::time::{self};

mod printdb;
use printdb::PrintDB;
#[cfg(feature = "rocksdb")]
mod rocksdb;
#[cfg(feature = "rocksdb")]
use rocksdb::RocksDB;
#[cfg(feature = "cassandra")]
mod cassandra;
#[cfg(feature = "cassandra")]
use cassandra::Cassandra;

const STATISTICS_SCALE: f64 = 1000.0;

pub type Key = [u8];
pub type Value = [u8];

trait Latency = Counter + AddAssign + Default;

struct Statistics {
    histogram: Histogram<u64>,
    failed_count: usize,
    count: usize,
    sum: u64,
}

impl Default for Statistics {
    fn default() -> Self {
        let mut histogram =
            Histogram::new_with_bounds(1, 60000000, 5).expect("Could not create histogram");
        histogram.auto(true);
        Self {
            histogram,
            failed_count: Default::default(),
            count: Default::default(),
            sum: Default::default(),
        }
    }
}

impl Statistics {
    fn add_latency(&mut self, latency_nanos: u128) {
        let latency_nanos: u64 = {
            let res: Result<u64, TryFromIntError> = latency_nanos.try_into();
            if let Ok(res) = res {
                res
            } else {
                eprintln!(
                    "[WARNING] Latency exceeds 2^64 nanoseconds, capping at 2^64 - 1 nanoseconds. This is a very large latency, please check your query."
                );
                u64::MAX
            }
        };
        self.count += 1;
        self.sum += latency_nanos;
        if self.histogram.record(latency_nanos).is_err() {
            eprintln!("Latency {} exceeds max latency", latency_nanos)
        };
    }

    fn add_error(&mut self) {
        self.failed_count += 1;
    }

    /// Returns the max latency in microseconds
    fn min(&self) -> f64 {
        return self.histogram.min() as f64 / STATISTICS_SCALE;
    }

    /// Returns the max latency in microseconds
    fn max(&self) -> f64 {
        return self.histogram.max() as f64 / STATISTICS_SCALE;
    }

    /// Returns the average latency in microseconds
    fn average(&self) -> f64 {
        return self.histogram.mean() / STATISTICS_SCALE;
    }

    /// Returns the total latency in microseconds
    fn total_latency(&self) -> f64 {
        return self.sum as f64 / STATISTICS_SCALE;
    }

    /// Returns the value at the given percentile in microseconds
    fn value_at_percentile(&self, percentile: f64) -> f64 {
        return self.histogram.value_at_percentile(percentile) as f64 / STATISTICS_SCALE;
    }
}
//     // TODO: Keep track of:
//     // 50th percentile latency for operations
//     //

pub fn execute_and_benchmark_db(db_layer: Db, input_file: String) -> Result<()> {
    let file = File::open(input_file)?;

    let file_size = file.metadata()?.len();

    eprintln!("[Executing]");
    let progress_bar =
        ProgressBar::with_draw_target(Some(file_size), ProgressDrawTarget::stderr_with_hz(5));
    progress_bar.set_style(ProgressStyle::default_bar().template("{bar:40} {percent}% ({eta})")?);

    let mut buf_reader = BufReader::new(progress_bar.wrap_read(file));

    let mut benchmarker = Benchmarker::new(db_layer);

    benchmarker.start();
    // for line in buf_reader.lines() {
    //     process_line(&line?, &mut benchmarker)?;
    // }

    let mut buf = Vec::<u8>::new();
    while buf_reader.read_until(b'\n', &mut buf)? > 0 {
        let end = buf.len() - 1;
        if buf[end] == b'\n' {
            buf.pop();
            if buf[end - 1] == b'\r' {
                buf.pop();
            }
        }
        // println!("Line: {}", unsafe {
        //     String::from_utf8_unchecked(buf.clone())
        // });

        process_line(&buf, &mut benchmarker)?;
        buf.clear();
    }

    benchmarker.end();
    progress_bar.finish_and_clear();

    // Print out statistics
    benchmarker.print_summary();

    return Ok(());
}

fn process_line(line: &[u8], benchmarker: &mut Benchmarker) -> Result<()> {
    let mut line_iter = line.split(|&b| b == b' ').filter(|s| !s.is_empty());
    let operation = match line_iter.next() {
        Some(op) => op,
        None => return Ok(()),
    };

    match operation {
        [b'I'] => {
            let key = line_iter.next().ok_or(anyhow!("Missing Argument"))?;
            let value = line_iter.next().ok_or(anyhow!("Missing Argument"))?;

            benchmarker.handle_insert(key, value)?;
        }
        [b'P'] => {
            let key = line_iter.next().ok_or(anyhow!("Missing Argument"))?;
            benchmarker.handle_point_query(key)?;
        }
        [b'U'] => {
            let key = line_iter.next().ok_or(anyhow!("Missing Argument"))?;
            let value = line_iter.next().ok_or(anyhow!("Missing Argument"))?;
            benchmarker.handle_update(key, value)?;
        }
        [b'M'] => {
            let key = line_iter.next().ok_or(anyhow!("Missing Argument"))?;
            let value = line_iter.next().ok_or(anyhow!("Missing Argument"))?;

            benchmarker.handle_merge(key, value)?;
        }
        [b'D'] => {
            let key = line_iter.next().ok_or(anyhow!("Missing Argument"))?;
            benchmarker.handle_point_delete(key)?;
        }
        [b'S', b'C'] => {
            let start_key = line_iter.next().ok_or(anyhow!("Missing Argument"))?;
            let count: usize =
                str::from_utf8(line_iter.next().ok_or(anyhow!("Missing Argument"))?)?.parse()?;

            benchmarker.handle_range_query_count(start_key, count)?;
        }
        [b'S'] => {
            let start_key = line_iter.next().ok_or(anyhow!("Missing Argument"))?;
            let bound = line_iter.next().ok_or(anyhow!("Missing Argument"))?;

            benchmarker.handle_range_query(start_key, bound)?;
        }
        [b'R', b'C'] => {
            // Range delete
            let start_key = line_iter.next().ok_or(anyhow!("Missing Argument"))?;
            let count: usize =
                str::from_utf8(line_iter.next().ok_or(anyhow!("Missing Argument"))?)?.parse()?;

            benchmarker.handle_range_delete_count(start_key, count)?;
        }
        [b'R'] => {
            // Range delete
            let start_key = line_iter.next().ok_or(anyhow!("Missing Argument"))?;
            let bound = line_iter.next().ok_or(anyhow!("Missing Argument"))?;

            benchmarker.handle_range_delete(start_key, bound)?;
        }
        [b'F', b'S'] => {
            let which_benchmarker = match line_iter.next().ok_or(anyhow!("Missing Argument"))? {
                [b'O'] => BenchmarkerType::Overall,
                [b'S'] => BenchmarkerType::Section,
                [b'G'] => BenchmarkerType::Group,
                _ => bail!("Unknown Benchmarker Type"),
            };
            benchmarker.start_stat_flush(which_benchmarker);
        }
        [b'F', b'E'] => {
            let which_benchmarker = match line_iter.next().ok_or(anyhow!("Missing Argument"))? {
                [b'O'] => BenchmarkerType::Overall,
                [b'S'] => BenchmarkerType::Section,
                [b'G'] => BenchmarkerType::Group,
                _ => bail!("Unknown Benchmarker Type"),
            };
            let remaining = line_iter.collect::<Vec<&[u8]>>().join(" ".as_bytes());
            if remaining.is_empty() {
                bail!("Missing Argument")
            }
            let name = str::from_utf8(&remaining)?;
            benchmarker.end_stat_flush(name, which_benchmarker)?;
        }
        _ => bail!(
            "Unknown operation \"{}\"",
            str::from_utf8(operation).context("Operation is not valid utf8")?
        ),
    };

    Ok(())
}

#[derive(Default)]
pub struct BenchmarkerInner<'a> {
    operation_statistics_map: HashMap<&'a str, Statistics>,
    start_time: Option<time::Instant>,
    end_time: Option<time::Instant>,
}

impl<'a> BenchmarkerInner<'a> {
    fn start(&mut self) {
        self.start_time = Some(time::Instant::now());
    }

    fn end(&mut self) {
        self.end_time = Some(time::Instant::now());
    }

    pub fn reset(&mut self) {
        self.start_time = None;
        self.end_time = None;
        self.operation_statistics_map.clear();
    }

    pub fn print_stats(&mut self) {
        let mut total_operation_counts = 0;
        let mut total_operation_timing_sum = 0.0;
        // Print out statistics
        for (&operation, stats) in self.operation_statistics_map.iter_mut() {
            total_operation_timing_sum += stats.total_latency();
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
            println!("[{}] Count: {:.5}", operation, stats.count);
            println!(
                "[{}] Successful Operations Count: {:.5}",
                operation,
                stats.count - stats.failed_count
            );
            println!(
                "[{}] Total Latency: {:.5}us",
                operation,
                stats.total_latency()
            );
            println!("[{}] Average Latency: {:.5}us", operation, stats.average());

            println!("[{}] Minimum Latency: {:.5}us", operation, stats.min());
            println!("[{}] Maximum Latency: {:.5}us", operation, stats.max());

            println!(
                "[{}] 95th Percentile Latency: {:.5}us",
                operation,
                stats.value_at_percentile(95.0)
            );

            println!(
                "[{}] 99th Percentile Latency: {:.5}us",
                operation,
                stats.value_at_percentile(99.0)
            );
        }

        if total_operation_timing_sum == 0.0 {
            eprintln!("No Operations");
            return;
        }

        println!("[Overall] Total Operations: {:.5}", total_operation_counts);
        println!(
            "[Overall] Average Latency: {:.5}us",
            total_operation_timing_sum / total_operation_counts as f64
        );

        if let (Some(start_time), Some(end_time)) = (self.start_time, self.end_time) {
            println!(
                "[Overall] Throughput (using start and end time): {:.5}ops/sec",
                total_operation_counts as f64 / end_time.duration_since(start_time).as_secs_f64()
            );
        }
        println!(
            "[Overall] Throughput (using aggregate operation times): {:.5}ops/sec",
            total_operation_counts as f64 / (total_operation_timing_sum / 1000000.0)
        );

        if let (Some(start_time), Some(end_time)) = (self.start_time, self.end_time) {
            println!(
                "[Overall] End to End Time: {:.5}secs",
                end_time.duration_since(start_time).as_secs_f64()
            );
        }
        println!(
            "[Overall] Aggregate Operation Time: {:.5}secs",
            total_operation_timing_sum / 1000000.0
        );
    }
}

macro_rules! measure {
    ($self:ident, $op_key:expr, $call:expr) => {
        let start_time = time::Instant::now();
        let res = $call;
        let latency = std::time::Instant::now()
            .duration_since(start_time)
            .as_nanos();
        let op_stats = $self
            .overall
            .operation_statistics_map
            .entry($op_key)
            .or_default();
        op_stats.add_latency(latency);

        if res.is_err() {
            op_stats.add_error();
        };

        if let Some(section_bencher) = &mut $self.section {
            let op_stats = section_bencher
                .operation_statistics_map
                .entry($op_key)
                .or_default();
            op_stats.add_latency(latency);
            if res.is_err() {
                op_stats.add_error();
            }
        }

        if let Some(group_bencher) = &mut $self.group {
            let op_stats = group_bencher
                .operation_statistics_map
                .entry($op_key)
                .or_default();
            op_stats.add_latency(latency);
            if res.is_err() {
                op_stats.add_error();
            }
        }
    };
}

pub enum BenchmarkerType {
    Overall,
    Section,
    Group,
}

pub struct Benchmarker<'a> {
    db_layer: Db,
    pub overall: BenchmarkerInner<'a>,
    pub section: Option<Box<BenchmarkerInner<'a>>>,
    pub group: Option<Box<BenchmarkerInner<'a>>>,
}

impl<'a> Benchmarker<'a> {
    pub fn new(db_layer: Db) -> Self {
        Self {
            db_layer,
            overall: BenchmarkerInner::default(),
            section: None,
            group: None,
        }
    }

    pub fn start(&mut self) {
        self.overall.start();
    }

    pub fn end(&mut self) {
        self.overall.end();
    }

    pub fn print_summary(&mut self) {
        println!("[[***Overall Stats***]]");
        self.overall.print_stats();
    }

    pub fn handle_insert(&mut self, key: &Key, value: &Value) -> Result<()> {
        // Generate value from string expression
        // Either insert value into database or write to file based on match
        measure!(self, "I", self.db_layer.insert(key, value));

        return Ok(());
    }

    pub fn handle_update(&mut self, key: &Key, value: &Value) -> Result<()> {
        measure!(self, "U", self.db_layer.update(key, value));

        return Ok(());
    }

    pub fn handle_merge(&mut self, key: &Key, value: &Value) -> Result<()> {
        measure!(self, "M", self.db_layer.merge(key, value));

        return Ok(());
    }

    pub fn handle_point_delete(&mut self, key: &Key) -> Result<()> {
        measure!(self, "D", self.db_layer.point_delete(key));

        return Ok(());
    }

    pub fn handle_point_query(&mut self, key: &Key) -> Result<()> {
        measure!(self, "P", self.db_layer.point_query(key));

        return Ok(());
    }

    pub fn handle_range_query(&mut self, key1: &Key, key2: &Key) -> Result<()> {
        measure!(self, "S", self.db_layer.range_query(key1, key2));

        return Ok(());
    }

    pub fn handle_range_query_count(&mut self, key1: &Key, count: usize) -> Result<()> {
        measure!(self, "S", self.db_layer.range_query_count(key1, count));
        return Ok(());
    }

    pub fn handle_range_delete(&mut self, start_key: &Key, end_key: &Key) -> Result<()> {
        measure!(self, "R", self.db_layer.range_delete(start_key, end_key));

        return Ok(());
    }

    pub fn handle_range_delete_count(&mut self, start_key: &Key, count: usize) -> Result<()> {
        measure!(
            self,
            "R",
            self.db_layer.range_delete_count(start_key, count)
        );

        return Ok(());
    }

    pub fn start_stat_flush(&mut self, benchmarker: BenchmarkerType) {
        let new = Box::new(BenchmarkerInner::default());

        let bench_ref = {
            match benchmarker {
                BenchmarkerType::Overall => {
                    panic!("Attempted to initialize flush of overall stats")
                }
                BenchmarkerType::Section => {
                    self.section = Some(new);
                    self.section
                        .as_mut()
                        .expect("Section benchmarker should be some")
                }
                BenchmarkerType::Group => {
                    self.group = Some(new);
                    self.group
                        .as_mut()
                        .expect("Group benchmarker should be some")
                }
            }
            .as_mut()
        };

        bench_ref.start();
    }

    pub fn end_stat_flush(&mut self, name: &str, benchmarker: BenchmarkerType) -> Result<()> {
        println!("[[***Stats for {}***]]", name);
        let benchmarker = match benchmarker {
            BenchmarkerType::Overall => bail!("Attempted to flush overall stats"),
            BenchmarkerType::Section => self
                .section
                .as_mut()
                .ok_or_else(|| anyhow!("No Section Benchmarker"))?,
            BenchmarkerType::Group => self
                .group
                .as_mut()
                .ok_or_else(|| anyhow!("No Group Benchmarker"))?,
        };
        benchmarker.end();
        benchmarker.print_stats();
        println!();
        benchmarker.reset();

        Ok(())
    }
}

#[enum_dispatch]
pub trait DBTranslationLayer {
    // Setup
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
    #[cfg(feature = "rocksdb")]
    RocksDB,
    #[cfg(feature = "cassandra")]
    Cassandra,
}

impl Db {
    pub fn new(database_name: &str, db_path: Option<&str>, config: Option<&str>) -> Result<Self> {
        Ok(match database_name {
            "printdb" => Self::PrintDB(PrintDB::new()?),
            // Err(err) => {
            //     bail!("Failed to create db because of error {err}");
            // }
            #[cfg(feature = "rocksdb")]
            "rocksdb" => Self::RocksDB(RocksDB::new(db_path, config)?),
            #[cfg(not(feature = "rocksdb"))]
            "rocksdb" => bail!("Rocksdb not enabled. Rebuild with --features rocksdb"),
            // Err(err) => {
            //     bail!("Failed to create db because of error {err}");
            // }
            #[cfg(feature = "cassandra")]
            "cassandra" => Self::Cassandra(Cassandra::new(db_path, config)?),
            #[cfg(not(feature = "cassandra"))]
            "cassandra" => bail!("Cassandra not enabled. Rebuild with --features cassandra"),
            _ => bail!("Unsupported database"),
        })
    }
}

pub fn execute_operations(
    name: &str,
    input_file: String,
    db_path: Option<&str>,
    config: Option<&str>,
) -> Result<()> {
    execute_and_benchmark_db(Db::new(name, db_path, config)?, input_file)
}
