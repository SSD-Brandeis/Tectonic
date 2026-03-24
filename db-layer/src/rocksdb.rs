use crate::Key;
use crate::{DBTranslationLayer, Value};
use anyhow::{Context, Result};
use rocksdb::{Options, ReadOptions, WriteOptions};
use std::env::temp_dir;
use std::fs::DirBuilder;
use std::path::PathBuf;

pub struct RocksDB {
    db: rocksdb::DB,
    point_read_opts: ReadOptions,
    write_opts: WriteOptions,
}

impl RocksDB {
    pub fn new(db_path: Option<PathBuf>, config_file_path: Option<&str>) -> Result<Self> {
        let dir = db_path.unwrap_or_else(|| {
            let mut dir = temp_dir();
            dir.push("tectonic-rocksdb/");
            dir
        });
        let dir_builder = DirBuilder::new();
        // This error means the directory already exists, which is what we want
        let _ = dir_builder.create(&dir);

        let opts = {
            if let Some(config_file_path) = config_file_path {
                let (opts, _) = Options::load_latest(
                    config_file_path,
                    rocksdb::Env::new()?,
                    false,
                    rocksdb::Cache::new_lru_cache(8 * 1024 * 1024),
                )
                .context("Failed to load RocksDB options file")?;
                opts
            } else {
                let mut opts = rocksdb::Options::default();
                let merge_fn = |_key: &[u8],
                                existing_value: Option<&[u8]>,
                                operands: &rocksdb::MergeOperands|
                 -> Option<Vec<u8>> {
                    let mut new = existing_value.map(|v| v.to_vec()).unwrap_or_default();
                    for op in operands {
                        new.extend_from_slice(op);
                    }

                    return Some(new);
                };
                opts.set_merge_operator_associative("Merge", merge_fn);
                opts.create_if_missing(true);
                opts
            }
        };

        Ok(Self {
            db: rocksdb::DB::open(&opts, dir.as_path())?,
            point_read_opts: ReadOptions::default(),
            write_opts: WriteOptions::default(),
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
        while iter.valid() {
            if limit.is_some_and(|limit| scanned >= limit) {
                break;
            }

            let _ = iter.item();
            scanned += 1;
            iter.next();
        }

        iter.status()?;

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

    fn point_query(&self, key: &Key) -> Result<()> {
        let _ = self.db.get_pinned_opt(key, &self.point_read_opts)?;
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
        self.db.merge_opt(key, value, &self.write_opts)?;
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
