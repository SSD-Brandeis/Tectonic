use crate::Key;
use crate::{DBTranslationLayer, Value};
use anyhow::{Context, Result};
use rocksdb::{Env, Options};
use std::collections::HashMap;
use std::env::temp_dir;
use std::fs::DirBuilder;
use std::path::PathBuf;

pub struct RocksDB {
    db: rocksdb::DB,
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

    fn point_query(&self, key: &Key) -> Result<()> {
        let _ = self.db.get(key);
        return Ok(());
    }

    fn update(&self, key: &Key, value: &Value) -> Result<()> {
        self.insert(key, value)?;
        return Ok(());
    }

    fn insert(&self, key: &Key, value: &Value) -> Result<()> {
        let _ = self.db.put(key, value);
        return Ok(());
    }

    fn range_query(&self, start_key: &Key, end_key: &Value) -> Result<()> {
        let mut opts = rocksdb::ReadOptions::default();
        opts.set_iterate_upper_bound(end_key.as_ref());
        let mut res = HashMap::<Box<[u8]>, Box<[u8]>>::new();
        let db_iter = self.db.iterator_opt(
            rocksdb::IteratorMode::From(start_key, rocksdb::Direction::Forward),
            opts,
        );

        for item in db_iter {
            let (key, value) = item?;
            res.insert(key, value);
        }
        return Ok(());
    }

    fn range_query_count(&self, start_key: &Key, range: usize) -> Result<()> {
        let mut db_iter = self.db.iterator(rocksdb::IteratorMode::From(
            start_key,
            rocksdb::Direction::Forward,
        ));
        let mut res = HashMap::<Box<[u8]>, Box<[u8]>>::new();
        for _ in 0..range {
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

    fn point_delete(&self, key: &Key) -> Result<()> {
        self.db.delete(key)?;
        return Ok(());
    }

    fn merge(&self, key: &Key, value: &Value) -> Result<()> {
        self.db.merge(key, value)?;
        return Ok(());
    }

    fn range_delete(&self, start_key: &Key, end_key: &Value) -> Result<()> {
        let mut write_batch = rocksdb::WriteBatch::default();
        write_batch.delete_range(start_key, end_key);
        self.db.write(write_batch)?;
        return Ok(());
    }

    fn range_delete_count(&self, start_key: &Key, range: usize) -> Result<()> {
        let mut db_iter = self.db.iterator(rocksdb::IteratorMode::From(
            start_key,
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
            write_batch.delete_range(start_key.as_ref(), end_key.as_ref());
            self.db.write(write_batch)?;
        }

        return Ok(());
    }
}
