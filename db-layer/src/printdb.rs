use crate::DBTranslationLayer;
use crate::{Key, Value};
use anyhow::Result;

pub struct PrintDB {}

impl PrintDB {
    pub fn new() -> Result<Self> {
        println!("Initialized");
        Ok(Self {})
    }
}

impl DBTranslationLayer for PrintDB {
    fn cleanup(self) -> Result<()> {
        println!("Done");

        return Ok(());
    }

    fn point_query(&self, key: &Key) -> Result<()> {
        let key = str::from_utf8(key)?;
        println!("PointQuery: {{key = {key}}}");

        return Ok(());
    }

    fn update(&self, key: &Key, value: &Value) -> Result<()> {
        let key = str::from_utf8(key)?;
        let value = str::from_utf8(value)?;
        println!("Update: {{key = {key}, value = {value}}}");

        return Ok(());
    }

    fn insert(&self, key: &Key, value: &Value) -> Result<()> {
        let key = str::from_utf8(key)?;
        let value = str::from_utf8(value)?;
        println!("Insert: {{key = {key}, value = {value}}}");

        return Ok(());
    }

    fn range_query(&self, start_key: &Key, end_key: &Key) -> Result<()> {
        let start_key = str::from_utf8(start_key)?;
        let end_key = str::from_utf8(end_key)?;
        println!("Range Query: {{start_key = {start_key}, end_key = {end_key}}}");

        return Ok(());
    }

    fn range_query_count(&self, start_key: &Key, range: usize) -> Result<()> {
        let start_key = str::from_utf8(start_key)?;
        println!("Range Query: {{key = {start_key}, count = {range}}}");

        return Ok(());
    }

    fn point_delete(&self, key: &Key) -> Result<()> {
        let key = str::from_utf8(key)?;
        println!(" Delete: {{key = {key}}}");

        return Ok(());
    }

    fn range_delete(&self, start_key: &Key, end_key: &Key) -> Result<()> {
        let start_key = str::from_utf8(start_key)?;
        let end_key = str::from_utf8(end_key)?;
        println!("Range Delete: {{start_key = {start_key}, end_key = {end_key}}}");

        return Ok(());
    }

    fn range_delete_count(&self, start_key: &Key, range: usize) -> Result<()> {
        let start_key = str::from_utf8(start_key)?;
        println!("Range Query: {{key = {start_key}, count = {range}}}");
        return Ok(());
    }

    fn merge(&self, key: &Key, value: &Value) -> Result<()> {
        let key = str::from_utf8(key)?;
        let value = str::from_utf8(value)?;
        println!("Merge: {{key = {key}, value = {value}}}");

        return Ok(());
    }
}
