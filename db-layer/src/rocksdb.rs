use crate::Key;
use crate::{DBTranslationLayer, Value};
use anyhow::{Context, Result};
use rocksdb::perf::{PerfContext, PerfStatsLevel, set_perf_stats};
use rocksdb::statistics::StatsLevel;
use rocksdb::{FlushOptions, Options, ReadOptions, WriteOptions};
use std::cell::{Cell, RefCell};
use std::env::temp_dir;
use std::ffi::{CStr, c_char, c_void};
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
    /// Config "all_stats=1": also print the executor thread's PerfContext and IOStatsContext at cleanup.
    all_stats: bool,
}

unsafe extern "C" {
    /// `src/rocksdb_iostats.cc`: the calling thread's IOStatsContext report, malloc'ed.
    fn xbench_iostats_context_report(exclude_zero_counters: u8) -> *mut c_char;
    fn free(ptr: *mut c_void);
}

fn iostats_context_report() -> String {
    unsafe {
        let ptr = xbench_iostats_context_report(1);
        let report = CStr::from_ptr(ptr).to_string_lossy().into_owned();
        free(ptr as *mut c_void);
        report
    }
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

/// Config "all_stats=1": every RocksDB stats collector on. Statistics at `StatsLevel::All` (all tickers and
/// histograms, timers included), `report_bg_io_stats` (flush and compaction I/O in the statistics), statistics
/// dumped to the LOG every 60 s, and the PerfContext and IOStatsContext of the executor thread at
/// `PerfStatsLevel::EnableTime` (counts and times, mutexes included), printed at cleanup. Implies
/// "statistics=1". The timers add work to every operation.
fn parse_all_stats(config: Option<&str>) -> bool {
    config.is_some_and(|config| config.split(';').any(|opt| opt.trim() == "all_stats=1"))
}

/// Config "direct_io=1": O_DIRECT for reads and for flush and compaction, bypassing the page cache, as
/// db_bench's `-use_direct_reads=true -use_direct_io_for_flush_and_compaction=true`.
fn parse_direct_io(config: Option<&str>) -> bool {
    config.is_some_and(|config| config.split(';').any(|opt| opt.trim() == "direct_io=1"))
}

/// Config "disable_wal=1": writes skip the write-ahead log (`WriteOptions::disable_wal`), so everything RocksDB
/// writes to disk goes through flush and compaction.
fn parse_disable_wal(config: Option<&str>) -> bool {
    config.is_some_and(|config| config.split(';').any(|opt| opt.trim() == "disable_wal=1"))
}

/// Config "sync=1": every write syncs the WAL before it returns (`WriteOptions::sync`).
fn parse_sync(config: Option<&str>) -> bool {
    config.is_some_and(|config| config.split(';').any(|opt| opt.trim() == "sync=1"))
}

/// The number of a "<key>=<n>" config option, if given.
fn parse_u64_option(config: Option<&str>, key: &str) -> Option<u64> {
    config?.split(';').find_map(|opt| opt.trim().strip_prefix(key)?.strip_prefix('=')?.parse().ok())
}

impl RocksDB {
    pub fn new(db_path: Option<&str>, config: Option<&str>) -> Result<Self> {
        // let db_path =
        let dir = {
            if let Some(db_path) = db_path {
                PathBuf::from_str(db_path).context("Not a valid database path")?
            } else {
                let mut dir = temp_dir();
                dir.push("xbench-rocksdb/");
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
        let all_stats = parse_all_stats(config);
        let statistics = all_stats || parse_statistics(config);
        if statistics {
            opts.enable_statistics();
        }
        if all_stats {
            opts.set_statistics_level(StatsLevel::All);
            opts.set_report_bg_io_stats(true);
            opts.set_stats_dump_period_sec(60);
            // PerfContext and IOStatsContext are per thread: this is the executor thread, which also runs the
            // operations and cleanup (db-layer/src/lib.rs).
            set_perf_stats(PerfStatsLevel::EnableTime);
        }
        if parse_direct_io(config) {
            opts.set_use_direct_reads(true);
            opts.set_use_direct_io_for_flush_and_compaction(true);
        }
        // Config "wal_bytes_per_sync=<n>": RocksDB has the kernel write the WAL out every n bytes in the background.
        if let Some(n) = parse_u64_option(config, "wal_bytes_per_sync") {
            opts.set_wal_bytes_per_sync(n);
        }
        // Config "wal_ttl_seconds=<n>": obsolete WAL files are moved to archive/ and kept n seconds, not deleted.
        if let Some(n) = parse_u64_option(config, "wal_ttl_seconds") {
            opts.set_wal_ttl_seconds(n);
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
            write_opts: {
                let mut write_opts = WriteOptions::default();
                write_opts.disable_wal(parse_disable_wal(config));
                write_opts.set_sync(parse_sync(config));
                write_opts
            },
            read_stats: ReadStats {
                scan_log: parse_scan_log(config).map(|path| (path, RefCell::new(Vec::new()))),
                ..Default::default()
            },
            stats_opts: statistics.then_some(opts),
            all_stats,
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
        if self.all_stats {
            if let Ok(Some(stats)) = self.db.property_value("rocksdb.levelstats") {
                println!("=== RocksDB Level Stats ===\n{}", stats);
            }
            if let Ok(Some(stats)) = self.db.property_value("rocksdb.aggregated-table-properties") {
                println!("=== RocksDB Aggregated Table Properties ===\n{}", stats);
            }
            println!("=== RocksDB Perf Context (executor thread) ===\n{}", PerfContext::default().report(true));
            println!("=== RocksDB IOStats Context (executor thread) ===\n{}", iostats_context_report());
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
