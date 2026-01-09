#![allow(clippy::needless_return)]
#![feature(duration_millis_float)]

use anyhow::{Error, Result, anyhow, bail};
use std::collections::HashMap;
use std::env::temp_dir;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::time::{self};

fn main() {
    // Ask for an input file (workflow file) and database layer
    // Init database layer
    // Read through the workflow file line by line and call operations from intialized database
    // layer
    //
    //
    // TODO: Keep track of:
    // average operation latency and for each operation
    // Total Throughout
    // Successful operations (think about point queries)
    // Number of operations for each operation and total
    // Min latency
    // Max latency
    // 50th percentile latency for operations
    // 95th percentile latency for operations
    // 99th percentile latency for operations
    //
    // TODO: Windowed version of operations for printing status?
    //
    // Easiest way to do this is a hashmap

    let args: Vec<String> = std::env::args().collect();
    if args.len() != 3 {
        println!("Format: {} <db_name> <workload_file>", { args[0].clone() });
        return;
    }

    let file_path = args[2].clone();

    let file = File::open(file_path).expect("Need a proper file path");
    let buf_reader = BufReader::new(file);

    let db_layer: Box<dyn DBTranslationLayer> = match &*args[1].clone().to_ascii_lowercase() {
        "printdb" => match PrintDB::new() {
            Ok(db) => Box::new(db),
            Err(err) => {
                eprintln!("Failed to create db because of error {err}");
                return;
            }
        },
        "rocksdb" => match RocksDB::new() {
            Ok(db) => Box::new(db),
            Err(err) => {
                eprintln!("Failed to create db because of error {err}");
                return;
            }
        },
        _ => panic!("Unsupported database. Supported databases are printdb and rocksdb"),
    };

    let mut operation_statistics = HashMap::<&str, Statistics>::new();

    let start_time = std::time::Instant::now();
    for line in buf_reader.lines() {
        let res = parse_line(line, db_layer.as_ref(), &mut operation_statistics);
        if res.is_err() {
            println!("{:#?}", res);
            return;
        }
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

fn parse_line(
    line: Result<String, std::io::Error>,
    db_layer: &dyn DBTranslationLayer,
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
        _ => bail!("Unknown operation"),
    };

    let latency = std::time::Instant::now()
        .duration_since(start_time)
        .as_millis_f64();
    let map = operation_statistics_map.entry(op_key).or_default();
    map.add_latency(latency);

    Ok(())
}

trait DBTranslationLayer {
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

struct PrintDB {}

impl PrintDB {
    fn new() -> Result<Self> {
        println!("Initialized");
        Ok(Self {})
    }
}

impl DBTranslationLayer for PrintDB {
    fn init(&mut self) -> Result<()> {
        println!("Initialized");
        Ok(())
    }

    fn cleanup(self) -> Result<()> {
        println!("Done");

        return Ok(());
    }

    fn point_query(&self, key: String) -> Result<()> {
        println!("PointQuery: {{key = {key}}}");

        return Ok(());
    }

    fn update(&self, key: String, value: String) -> Result<()> {
        println!("Update: {{key = {key}, value = {value}}}");

        return Ok(());
    }

    fn insert(&self, key: String, value: String) -> Result<()> {
        println!("Insert: {{key = {key}, value = {value}}}");

        return Ok(());
    }

    fn range_query(&self, start_key: String, end_key: String) -> Result<()> {
        println!("Range Query: {{start_key = {start_key}, end_key = {end_key}}}");

        return Ok(());
    }

    fn range_query_count(&self, start_key: String, range: usize) -> Result<()> {
        println!("Range Query: {{key = {start_key}, count = {range}}}");

        return Ok(());
    }

    fn point_delete(&self, key: String) -> Result<()> {
        println!(" Delete: {{key = {key}}}");

        return Ok(());
    }

    fn range_delete(&self, start_key: String, end_key: String) -> Result<()> {
        println!("Range Delete: {{start_key = {start_key}, end_key = {end_key}}}");

        return Ok(());
    }

    fn range_delete_count(&self, start_key: String, range: usize) -> Result<()> {
        println!("Range Query: {{key = {start_key}, count = {range}}}");
        return Ok(());
    }

    fn merge(&self, key: String, value: String) -> Result<()> {
        println!("Merge: {{key = {key}, value = {value}}}");

        return Ok(());
    }
}

struct RocksDB {
    db: rocksdb::DB,
}

impl RocksDB {
    fn new() -> Result<Self, Error> {
        let dir = temp_dir();
        Ok(Self {
            db: rocksdb::DB::open_default(dir.as_path())?,
        })
    }
}

impl DBTranslationLayer for RocksDB {
    fn init(&mut self) -> Result<()> {
        let dir = temp_dir();
        self.db = rocksdb::DB::open_default(dir.as_path())?;

        Ok(())
    }

    fn cleanup(self) -> Result<()> {
        std::mem::drop(self);
        return Ok(());
    }

    fn point_query(&self, key: String) -> Result<()> {
        let _ = self.db.get(key);
        return Ok(());
    }

    fn update(&self, key: String, value: String) -> Result<()> {
        self.insert(key, value)?;
        return Ok(());
    }

    fn insert(&self, key: String, value: String) -> Result<()> {
        let _ = self.db.put(key.clone(), value.clone());
        return Ok(());
    }

    fn range_query(&self, start_key: String, end_key: String) -> Result<()> {
        let mut opts = rocksdb::ReadOptions::default();
        opts.set_iterate_upper_bound(end_key);
        let mut res = HashMap::<Box<[u8]>, Box<[u8]>>::new();
        let db_iter = self.db.iterator_opt(
            rocksdb::IteratorMode::From(start_key.as_bytes(), rocksdb::Direction::Forward),
            opts,
        );

        for item in db_iter {
            let (key, value) = item?;
            res.insert(key, value);
        }
        return Ok(());
    }

    fn range_query_count(&self, start_key: String, range: usize) -> Result<()> {
        let mut db_iter = self.db.iterator(rocksdb::IteratorMode::From(
            start_key.as_bytes(),
            rocksdb::Direction::Forward,
        ));
        let mut res = HashMap::<Box<[u8]>, Box<[u8]>>::new();
        for i in 0..range {
            let item = db_iter.next();
            if let Some(item) = item {
                let (key, value) = item?;
                res.insert(key, value);
            } else {
                break;
            }
        }
        return Ok(());
    }

    fn point_delete(&self, key: String) -> Result<()> {
        self.db.delete(key)?;
        return Ok(());
    }

    fn merge(&self, key: String, value: String) -> Result<()> {
        self.db.merge(key, value)?;
        return Ok(());
    }

    fn range_delete(&self, start_key: String, end_key: String) -> Result<()> {
        let mut write_batch = rocksdb::WriteBatch::default();
        write_batch.delete_range(start_key.as_bytes(), end_key.as_bytes());
        self.db.write(write_batch)?;
        return Ok(());
    }

    fn range_delete_count(&self, start_key: String, range: usize) -> Result<()> {
        let mut db_iter = self.db.iterator(rocksdb::IteratorMode::From(
            start_key.as_bytes(),
            rocksdb::Direction::Forward,
        ));
        let mut end_key: Option<Box<[u8]>> = None;
        for i in 0..range {
            if let Some(item) = db_iter.next() {
                let (key, _) = item?;
                end_key = Some(key);
                if i == range - 1 {
                    break;
                }
            } else {
                break;
            }
        }

        if let Some(end_key) = end_key {
            let mut write_batch = rocksdb::WriteBatch::default();
            write_batch.delete_range(start_key.as_bytes(), end_key.as_ref());
            self.db.write(write_batch)?;
        }

        return Ok(());
    }
}
