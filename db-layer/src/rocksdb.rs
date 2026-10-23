use crate::Key;
use crate::{DBTranslationLayer, Value};
use anyhow::{Context, Result};
use rocksdb::{FlushOptions, Options, ReadOptions, WriteOptions};
use std::cell::{Cell, RefCell};
use std::env::temp_dir;
use std::fs::DirBuilder;
use std::io::Write;
use std::path::PathBuf;
use std::str::FromStr;

pub struct RocksDB {
    db: rocksdb::DB,
    point_read_opts: ReadOptions,
    write_opts: WriteOptions,
    read_stats: ReadStats,
    /// The DB options when RocksDB statistics are enabled (config "statistics=1"), to print them at cleanup.
    stats_opts: Option<Options>,
}

/// What the reads of a workload returned, printed at cleanup.
#[derive(Default)]
struct ReadStats {
    point_query_hits: Cell<u64>,
    point_query_bytes: Cell<u64>,
    append_read_bytes: Cell<u64>,
    range_query_keys: Cell<u64>,
    range_query_bytes: Cell<u64>,
    /// Keys returned by every range query, written to `scan_log` (config "scan_log=<path>").
    scan_log: Option<(String, RefCell<Vec<u32>>)>,
}

fn parse_scan_log(config: Option<&str>) -> Option<String> {
    config?
        .split(';')
        .find_map(|opt| opt.trim().strip_prefix("scan_log=").map(String::from))
}

fn parse_statistics(config: Option<&str>) -> bool {
    config.is_some_and(|config| config.split(';').any(|opt| opt.trim() == "statistics=1"))
}

impl RocksDB {
    pub fn new(db_path: Option<&str>, config: Option<&str>) -> Result<Self> {
        // let db_path =
        let dir = {
            if let Some(db_path) = db_path {
                PathBuf::from_str(db_path).context("Not a valid database path")?
            } else {
                let mut dir = temp_dir();
                dir.push("tectonic-rocksdb/");
                dir
            }
        };

        let dir_builder = DirBuilder::new();
        // This error means the directory already exists, which is what we want
        let _ = dir_builder.create(&dir);

        let mut opts = rocksdb::Options::default();
        opts.create_if_missing(true);
        opts.set_allow_concurrent_memtable_write(false);
        opts.set_write_buffer_size(64 * 1024 * 1024);
        opts.set_max_write_buffer_number(4);
        opts.set_target_file_size_base(64 * 1024 * 1024);
        opts.set_level_zero_file_num_compaction_trigger(4);
        let statistics = parse_statistics(config);
        if statistics {
            opts.enable_statistics();
        }

        // let merge_fn = |_key: &[u8],
        //                 existing_value: Option<&[u8]>,
        //                 operands: &rocksdb::MergeOperands|
        //  -> Option<Vec<u8>> {
        //     let mut new = existing_value.map(|v| v.to_vec()).unwrap_or_default();
        //     for op in operands {
        //         new.extend_from_slice(op);
        //     }
        //
        //     return Some(new);
        // };
        // opts.set_merge_operator_associative("Merge", merge_fn);

        Ok(Self {
            db: rocksdb::DB::open(&opts, dir.as_path())?,
            point_read_opts: ReadOptions::default(),
            write_opts: WriteOptions::default(),
            read_stats: ReadStats {
                scan_log: parse_scan_log(config).map(|path| (path, RefCell::new(Vec::new()))),
                ..Default::default()
            },
            stats_opts: statistics.then_some(opts),
        })
    }

    fn scan(&self, start_key: &Key, end_key: Option<&[u8]>, limit: Option<usize>) -> Result<()> {
        if matches!(limit, Some(0)) {
            return Ok(());
        }

        let mut opts = ReadOptions::default();
        if let Some(end_key) = end_key {
            opts.set_iterate_upper_bound(end_key.to_vec());
        }

        let mut iter = self.db.raw_iterator_opt(opts);
        iter.seek(start_key);

        let mut scanned = 0usize;
        let mut bytes = 0usize;
        while iter.valid() {
            if limit.is_some_and(|limit| scanned >= limit) {
                break;
            }

            if let Some((key, value)) = iter.item() {
                bytes += key.len() + value.len();
            }
            scanned += 1;
            iter.next();
        }

        iter.status()?;

        let stats = &self.read_stats;
        stats.range_query_keys.set(stats.range_query_keys.get() + scanned as u64);
        stats.range_query_bytes.set(stats.range_query_bytes.get() + bytes as u64);
        if let Some((_, log)) = &stats.scan_log {
            log.borrow_mut().push(scanned as u32);
        }

        Ok(())
    }

    fn last_key_in_scan(&self, start_key: &Key, limit: usize) -> Result<Option<Vec<u8>>> {
        if limit == 0 {
            return Ok(None);
        }

        let mut iter = self.db.raw_iterator();
        iter.seek(start_key);

        let mut scanned = 0usize;
        while iter.valid() && scanned < limit - 1 {
            scanned += 1;
            iter.next();
        }
        let last_key = iter.key().map(|key| key.to_vec());

        iter.status()?;

        Ok(last_key)
    }
}

impl Drop for RocksDB {
    fn drop(&mut self) {
        let mut flush_opts = FlushOptions::default();
        flush_opts.set_wait(true);
        let _ = self.db.flush_opt(&flush_opts);
    }
}

impl DBTranslationLayer for RocksDB {
    fn cleanup(&mut self) -> Result<()> {
        let mut flush_opts = FlushOptions::default();
        flush_opts.set_wait(true);
        let _ = self.db.flush_opt(&flush_opts);
        let rs = &self.read_stats;
        println!("=== Executor Read Stats ===");
        println!("Point query hits: {}", rs.point_query_hits.get());
        println!("Point query bytes returned: {}", rs.point_query_bytes.get());
        println!("Append bytes read: {}", rs.append_read_bytes.get());
        println!("Range query keys returned: {}", rs.range_query_keys.get());
        println!("Range query bytes returned: {}", rs.range_query_bytes.get());
        if let Some((path, log)) = &rs.scan_log {
            let mut out = std::io::BufWriter::new(std::fs::File::create(path)?);
            for count in log.borrow().iter() {
                writeln!(out, "{count}")?;
            }
        }
        if let Ok(Some(stats)) = self.db.property_value("rocksdb.stats") {
            println!("=== RocksDB Internal Stats ===\n{}", stats);
        }
        if let Some(stats) = self.stats_opts.as_ref().and_then(|opts| opts.get_statistics()) {
            println!("=== RocksDB Statistics ===\n{}", stats);
        }
        return Ok(());
    }

    fn point_query(&self, key: &Key) -> Result<()> {
        if let Some(value) = self.db.get_pinned_opt(key, &self.point_read_opts)? {
            let stats = &self.read_stats;
            stats.point_query_hits.set(stats.point_query_hits.get() + 1);
            stats.point_query_bytes.set(stats.point_query_bytes.get() + value.len() as u64);
        }
        return Ok(());
    }

    fn update(&self, key: &Key, value: &Value) -> Result<()> {
        self.insert(key, value)?;
        return Ok(());
    }

    fn insert(&self, key: &Key, value: &Value) -> Result<()> {
        self.db.put_opt(key, value, &self.write_opts)?;
        return Ok(());
    }

    fn range_query(&self, start_key: &Key, end_key: &Value) -> Result<()> {
        self.scan(start_key, Some(end_key), None)?;
        return Ok(());
    }

    fn range_query_count(&self, start_key: &Key, range: usize) -> Result<()> {
        self.scan(start_key, None, Some(range))?;
        return Ok(());
    }

    fn point_delete(&self, key: &Key) -> Result<()> {
        self.db.delete_opt(key, &self.write_opts)?;
        return Ok(());
    }

    fn merge(&self, key: &Key, value: &Value) -> Result<()> {
        // self.db.merge_opt(key, value, &self.write_opts)?;
        self.point_query(key)?;
        self.update(key, value)?;

        return Ok(());
    }

    fn append(&self, key: &Key, value: &Value) -> Result<()> {
        let mut new = self
            .db
            .get_pinned_opt(key, &self.point_read_opts)?
            .map(|v| v.to_vec())
            .unwrap_or_default();
        let stats = &self.read_stats;
        stats.append_read_bytes.set(stats.append_read_bytes.get() + new.len() as u64);
        new.extend_from_slice(value);
        self.db.put_opt(key, new, &self.write_opts)?;

        return Ok(());
    }

    fn range_delete(&self, start_key: &Key, end_key: &Value) -> Result<()> {
        let mut write_batch = rocksdb::WriteBatch::default();
        write_batch.delete_range(start_key, end_key);
        self.db.write_opt(write_batch, &self.write_opts)?;
        return Ok(());
    }

    fn range_delete_count(&self, start_key: &Key, range: usize) -> Result<()> {
        let end_key = self.last_key_in_scan(start_key, range)?;

        if let Some(end_key) = end_key {
            let mut write_batch = rocksdb::WriteBatch::default();
            write_batch.delete_range(start_key.as_ref(), end_key.as_ref());
            self.db.write_opt(write_batch, &self.write_opts)?;
        }

        return Ok(());
    }
}
