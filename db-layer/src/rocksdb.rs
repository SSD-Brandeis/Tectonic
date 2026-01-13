use crate::DBTranslationLayer;
use anyhow::Result;
use std::collections::HashMap;
use std::env::temp_dir;

pub struct RocksDB {
    db: rocksdb::DB,
}

impl RocksDB {
    pub fn new() -> Result<Self> {
        let dir = temp_dir();
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
