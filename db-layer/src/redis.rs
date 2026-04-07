use crate::Key;
use crate::{DBTranslationLayer, Value};
use anyhow::anyhow;
use anyhow::{Context, Result};
use redis::{Client, Commands, Connection};
use std::cell::RefCell;

pub struct Redis {
    client: Client,
    conn: RefCell<Connection>,
}

impl Redis {
    pub fn new(endpoint: Option<&str>, config_string: Option<&str>) -> Result<Self> {
        let endpoint =
            endpoint.ok_or_else(|| anyhow!("Redis requires at least one endpoint to work"))?;
        let client = Client::open(endpoint)?;
        let conn = RefCell::new(client.get_connection()?);

        Ok(Self { client, conn })
    }
}

impl DBTranslationLayer for Redis {
    fn cleanup(self) -> Result<()> {
        todo!()
    }

    fn insert(&self, key: &Key, value: &Value) -> Result<()> {
        let _: () = self.conn.borrow_mut().set(key, value)?;

        Ok(())
    }

    fn update(&self, key: &Key, value: &Value) -> Result<()> {
        let _: () = self.conn.borrow_mut().set(key, value)?;

        Ok(())
    }

    fn merge(&self, key: &Key, value: &Value) -> Result<()> {
        self.point_query(key)?;
        self.update(key, value)?;

        Ok(())
    }

    fn point_delete(&self, key: &Key) -> Result<()> {
        let _: () = self.conn.borrow_mut().del(key)?;

        Ok(())
    }

    fn point_query(&self, key: &Key) -> Result<()> {
        let _: () = self.conn.borrow_mut().get(key)?;

        Ok(())
    }

    fn range_query(&self, start_key: &Key, end_key: &Value) -> Result<()> {
        // NOTE: Redis does not sort its keyspace, so I'm not sure we should allow scanning
        todo!()
    }

    fn range_query_count(&self, start_key: &Key, range: usize) -> Result<()> {
        todo!()
    }

    fn range_delete(&self, start_key: &Key, end_key: &Key) -> Result<()> {
        todo!()
    }

    fn range_delete_count(&self, start_key: &Key, range: usize) -> Result<()> {
        todo!()
    }
}
