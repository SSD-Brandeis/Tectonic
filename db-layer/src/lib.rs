#![allow(clippy::needless_return)]
#![feature(duration_millis_float, trait_alias)]

use anyhow::{Context, Result, anyhow, bail};
use enum_dispatch::enum_dispatch;
use hdrhistogram::{Counter, Histogram};
use indicatif::{ProgressBar, ProgressDrawTarget, ProgressStyle};
use std::collections::HashMap;
use std::fs::File;
use std::path::PathBuf;
use std::io::{BufRead, BufReader, Write};
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
#[cfg(feature = "redis")]
mod redis;
#[cfg(feature = "redis")]
use redis::Redis;
#[cfg(feature = "scylla")]
mod scylla;
#[cfg(feature = "scylla")]
use scylla::Scylla;

const STATISTICS_SCALE: f64 = 1000.0;

pub type Key = [u8];
pub type Value = [u8];

trait Latency = Counter + AddAssign + Default;

#[derive(Clone)]
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
    pub fn merge(&mut self, other: &Statistics) -> Result<()> {
        self.count += other.count;
        self.sum += other.sum;
        self.failed_count += other.failed_count;
        self.histogram.add(&other.histogram).map_err(|e| anyhow!("Failed to merge histograms: {:?}", e))?;
        Ok(())
    }

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

pub fn execute_and_benchmark_db(
    database_name: &str,
    input_file: String,
    db_path: Option<&str>,
    config: Option<&str>,
    threads: usize,
    target_rate: Option<u64>,
) -> Result<()> {
    eprintln!("[Executing]");

    let thread_rate = target_rate.map(|r| r / (threads as u64).max(1));
    let mut handles = Vec::new();

    let db_name = database_name.to_string();
    let db_path = db_path.map(String::from);
    let config = config.map(String::from);

    for t in 0..threads {
        let db_name_clone = db_name.clone();
        let db_path_clone = db_path.clone();
        let config_clone = config.clone();
        
        let mut final_path = PathBuf::from(&input_file);
        if threads > 1 {
            let ext = final_path.extension().unwrap_or_default().to_string_lossy();
            let new_ext = if ext.is_empty() { format!("{}", t) } else { format!("{}.{}", ext, t) };
            final_path.set_extension(new_ext);
        }

        let handle = std::thread::spawn(move || -> Result<Benchmarker<'static>> {
            let thread_db_path = if db_name_clone == "rocksdb" && threads > 1 {
                db_path_clone.as_ref().map(|path| format!("{}_{}", path, t))
            } else {
                db_path_clone.clone()
            };
            let db = Db::new(&db_name_clone, thread_db_path.as_deref(), config_clone.as_deref())?;
            let mut benchmarker = Benchmarker::new(db);
            benchmarker.start();

            let file = File::open(&final_path)?;
            let file_size = file.metadata()?.len();

            let progress_bar = if threads == 1 || t == 0 {
                let pb = ProgressBar::with_draw_target(Some(file_size), ProgressDrawTarget::stderr_with_hz(5));
                pb.set_style(ProgressStyle::default_bar().template("{bar:40} {percent}% ({eta})")?);
                Some(pb)
            } else {
                None
            };

            let mut buf_reader = if let Some(pb) = &progress_bar {
                BufReader::new(Box::new(pb.clone().wrap_read(file)) as Box<dyn std::io::Read>)
            } else {
                BufReader::new(Box::new(file) as Box<dyn std::io::Read>)
            };

            let mut buf = Vec::<u8>::new();
            let mut operations_done = 0;
            let start_time = time::Instant::now();

            while buf_reader.read_until(b'\n', &mut buf)? > 0 {
                let end = buf.len() - 1;
                if buf[end] == b'\n' {
                    buf.pop();
                    if !buf.is_empty() && buf[buf.len() - 1] == b'\r' {
                        buf.pop();
                    }
                }

                process_line(&buf, &mut benchmarker)?;
                buf.clear();

                operations_done += 1;
                if let Some(rate) = thread_rate {
                    let expected_time = std::time::Duration::from_secs_f64(operations_done as f64 / rate as f64);
                    let elapsed = start_time.elapsed();
                    if elapsed < expected_time {
                        std::thread::sleep(expected_time - elapsed);
                    }
                }
            }

            benchmarker.end();
            if let Some(pb) = progress_bar {
                pb.finish_and_clear();
            }

            Ok(benchmarker)
        });
        handles.push(handle);
    }

    let mut benchmarkers = Vec::new();
    for handle in handles {
        benchmarkers.push(handle.join().map_err(|e| anyhow!("Thread panicked: {:?}", e))??);
    }

    let mut merged_benchmarker = benchmarkers.remove(0);
    for b in benchmarkers {
        merged_benchmarker.overall.merge(&b.overall)?;
        if let Some(other_section) = b.section {
            if let Some(my_section) = &mut merged_benchmarker.section {
                my_section.merge(&other_section)?;
            } else {
                merged_benchmarker.section = Some(other_section);
            }
        }
        if let Some(other_group) = b.group {
            if let Some(my_group) = &mut merged_benchmarker.group {
                my_group.merge(&other_group)?;
            } else {
                merged_benchmarker.group = Some(other_group);
            }
        }
    }

    // Print out statistics
    merged_benchmarker.print_summary();
    merged_benchmarker.cleanup()?;

    Ok(())
}

fn process_line(line: &[u8], benchmarker: &mut Benchmarker) -> Result<()> {
    use std::str;

    // Find the first space to split the operation from the rest of the line
    let first_space = line.iter().position(|&b| b == b' ');
    let (operation, rest) = match first_space {
        Some(pos) => (&line[..pos], &line[pos + 1..]),
        None => (line, &[][..]),
    };

    if operation.is_empty() {
        return Ok(());
    }

    match operation {
        [b'I'] => {
            let mut parts = rest.splitn(2, |&b| b == b' ');
            let key = parts.next().ok_or_else(|| anyhow!("Missing Key for I"))?;
            let value = parts.next().ok_or_else(|| anyhow!("Missing Value for I"))?;
            benchmarker.handle_insert(key, value)?;
        }
        [b'U'] => {
            let mut parts = rest.splitn(2, |&b| b == b' ');
            let key = parts.next().ok_or_else(|| anyhow!("Missing Key for U"))?;
            let value = parts.next().ok_or_else(|| anyhow!("Missing Value for U"))?;
            benchmarker.handle_update(key, value)?;
        }
        [b'M'] => {
            let mut parts = rest.splitn(2, |&b| b == b' ');
            let key = parts.next().ok_or_else(|| anyhow!("Missing Key for M"))?;
            let value = parts.next().ok_or_else(|| anyhow!("Missing Value for M"))?;
            benchmarker.handle_merge(key, value)?;
        }
        _ => {
            // For other operations, we can split using space
            let mut line_iter = rest.split(|&b| b == b' ').filter(|s| !s.is_empty());
            match operation {
                [b'P'] => {
                    let key = line_iter.next().ok_or_else(|| anyhow!("Missing Argument"))?;
                    benchmarker.handle_point_query(key)?;
                }
                [b'B', b'P'] => {
                    let key = line_iter.next().ok_or_else(|| anyhow!("Missing Argument"))?;
                    benchmarker.handle_point_query(key)?;
                }
                [b'B', b'D'] => {
                    let key = line_iter.next().ok_or_else(|| anyhow!("Missing Argument"))?;
                    benchmarker.handle_point_delete(key)?;
                }
                [b'B', b'R'] => {
                    let start_key = line_iter.next().ok_or_else(|| anyhow!("Missing Argument"))?;
                    let count: usize = str::from_utf8(
                        line_iter.next().ok_or_else(|| anyhow!("Missing Argument"))?
                    )?.parse()?;
                    benchmarker.handle_range_query_count(start_key, count)?;
                }
                [b'D'] => {
                    let key = line_iter.next().ok_or_else(|| anyhow!("Missing Argument"))?;
                    benchmarker.handle_point_delete(key)?;
                }
                [b'S', b'C'] => {
                    let start_key = line_iter.next().ok_or_else(|| anyhow!("Missing Argument"))?;
                    let count: usize = str::from_utf8(
                        line_iter.next().ok_or_else(|| anyhow!("Missing Argument"))?
                    )?.parse()?;
                    benchmarker.handle_range_query_count(start_key, count)?;
                }
                [b'S'] => {
                    let start_key = line_iter.next().ok_or_else(|| anyhow!("Missing Argument"))?;
                    let bound = line_iter.next().ok_or_else(|| anyhow!("Missing Argument"))?;
                    benchmarker.handle_range_query(start_key, bound)?;
                }
                [b'R', b'C'] => {
                    let start_key = line_iter.next().ok_or_else(|| anyhow!("Missing Argument"))?;
                    let count: usize = str::from_utf8(
                        line_iter.next().ok_or_else(|| anyhow!("Missing Argument"))?
                    )?.parse()?;
                    benchmarker.handle_range_delete_count(start_key, count)?;
                }
                [b'R'] => {
                    let start_key = line_iter.next().ok_or_else(|| anyhow!("Missing Argument"))?;
                    let bound = line_iter.next().ok_or_else(|| anyhow!("Missing Argument"))?;
                    benchmarker.handle_range_delete(start_key, bound)?;
                }
                [b'F', b'S'] => {
                    let which_benchmarker = match line_iter.next().ok_or_else(|| anyhow!("Missing Argument"))? {
                        [b'O'] => BenchmarkerType::Overall,
                        [b'S'] => BenchmarkerType::Section,
                        [b'G'] => BenchmarkerType::Group,
                        _ => bail!("Unknown Benchmarker Type"),
                    };
                    benchmarker.start_stat_flush(which_benchmarker);
                }
                [b'F', b'E'] => {
                    let which_benchmarker = match line_iter.next().ok_or_else(|| anyhow!("Missing Argument"))? {
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
            }
        }
    }
    Ok(())
}

#[derive(Default, Clone)]
pub struct BenchmarkerInner<'a> {
    operation_statistics_map: HashMap<&'a str, Statistics>,
    start_time: Option<time::Instant>,
    end_time: Option<time::Instant>,
}

impl<'a> BenchmarkerInner<'a> {
    pub fn merge(&mut self, other: &BenchmarkerInner<'a>) -> Result<()> {
        for (op, stats) in &other.operation_statistics_map {
            if let Some(my_stats) = self.operation_statistics_map.get_mut(op) {
                my_stats.merge(stats)?;
            } else {
                self.operation_statistics_map.insert(op, stats.clone());
            }
        }
        if let Some(other_start) = other.start_time {
            if let Some(my_start) = self.start_time {
                if other_start < my_start {
                    self.start_time = Some(other_start);
                }
            } else {
                self.start_time = Some(other_start);
            }
        }
        if let Some(other_end) = other.end_time {
            if let Some(my_end) = self.end_time {
                if other_end > my_end {
                    self.end_time = Some(other_end);
                }
            } else {
                self.end_time = Some(other_end);
            }
        }
        Ok(())
    }

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
                "[{}] 25th Percentile Latency: {:.5}us",
                operation,
                stats.value_at_percentile(25.0)
            );

            println!(
                "[{}] 50th Percentile Latency: {:.5}us",
                operation,
                stats.value_at_percentile(50.0)
            );

            println!(
                "[{}] 75th Percentile Latency: {:.5}us",
                operation,
                stats.value_at_percentile(75.0)
            );

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
            eprintln!("[ERROR]: {:#?}", res);
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

    pub fn cleanup(self) -> Result<()> {
        self.db_layer.cleanup()
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
    #[cfg(feature = "redis")]
    Redis,
    #[cfg(feature = "scylla")]
    Scylla,
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
            #[cfg(feature = "redis")]
            "redis" => Self::Redis(Redis::new(db_path, config)?),
            #[cfg(not(feature = "redis"))]
            "redis" => bail!("Redis not enabled. Rebuild with --features redis"),
            #[cfg(feature = "scylla")]
            "scylla" => Self::Scylla(Scylla::new(db_path, config)?),
            #[cfg(not(feature = "scylla"))]
            "scylla" => bail!("Scylla not enabled. Rebuild with --features scylla"),
            _ => bail!("Unsupported database"),
        })
    }
}

pub fn execute_operations(
    name: &str,
    input_file: String,
    db_path: Option<&str>,
    config: Option<&str>,
    threads: usize,
    target_rate: Option<u64>,
) -> Result<()> {
    execute_and_benchmark_db(name, input_file, db_path, config, threads, target_rate)
}
