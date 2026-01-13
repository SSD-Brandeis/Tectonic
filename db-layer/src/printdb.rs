use crate::DBTranslationLayer;
use anyhow::Result;

pub struct PrintDB {}

impl PrintDB {
    pub fn new() -> Result<Self> {
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
