use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use crate::{DBTranslationLayer, Key, Value};
use anyhow::Result;

pub struct HashMapDB {
    data: Arc<Mutex<HashMap<Vec<u8>, Vec<u8>>>>,
    pub misses: Arc<std::sync::atomic::AtomicUsize>,
    pub hits: Arc<std::sync::atomic::AtomicUsize>,
}

impl HashMapDB {
    pub fn new() -> Self {
        Self {
            data: Arc::new(Mutex::new(HashMap::new())),
            misses: Arc::new(std::sync::atomic::AtomicUsize::new(0)),
            hits: Arc::new(std::sync::atomic::AtomicUsize::new(0)),
        }
    }
}

impl DBTranslationLayer for HashMapDB {
    fn cleanup(self) -> Result<()> {
        Ok(())
    }

    fn point_query(&self, key: &Key) -> Result<()> {
        let data = self.data.lock().unwrap();
        if data.contains_key(key) {
            self.hits.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        } else {
            self.misses.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        }
        Ok(())
    }

    fn update(&self, key: &Key, value: &Value) -> Result<()> {
        self.insert(key, value)
    }

    fn insert(&self, key: &Key, value: &Value) -> Result<()> {
        let mut data = self.data.lock().unwrap();
        data.insert(key.to_vec(), value.to_vec());
        Ok(())
    }

    fn range_query(&self, _start_key: &Key, _end_key: &Value) -> Result<()> { Ok(()) }
    fn range_query_count(&self, _start_key: &Key, _range: usize) -> Result<()> { Ok(()) }
    fn point_delete(&self, key: &Key) -> Result<()> {
        let mut data = self.data.lock().unwrap();
        data.remove(key);
        Ok(())
    }
    fn merge(&self, key: &Key, value: &Value) -> Result<()> { self.insert(key, value) }
    fn range_delete(&self, _start_key: &Key, _end_key: &Value) -> Result<()> { Ok(()) }
    fn range_delete_count(&self, _start_key: &Key, _range: usize) -> Result<()> { Ok(()) }
}
