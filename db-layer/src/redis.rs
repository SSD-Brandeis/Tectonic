use crate::Key;
use crate::{DBTranslationLayer, Value};
use anyhow::Result;
use anyhow::anyhow;
use redis::{Client, Commands, Connection};
use std::cell::RefCell;

pub struct Redis {
    _client: Client,
    conn: RefCell<Connection>,
}

impl Redis {
    pub fn new(endpoint: Option<&str>, _config_string: Option<&str>) -> Result<Self> {
        let endpoint = endpoint.unwrap_or("redis://127.0.0.1/");
        let client = Client::open(endpoint)?;
        let conn = RefCell::new(client.get_connection()?);

        Ok(Self {
            _client: client,
            conn,
        })
    }
}

impl DBTranslationLayer for Redis {
    fn cleanup(self) -> Result<()> {
        drop(self);
        Ok(())
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

    fn range_query(&self, _start_key: &Key, _end_key: &Value) -> Result<()> {
        // NOTE: Redis does not sort its keyspace, so I'm not sure we should allow scanning
        return Err(anyhow!("Redis does not support range operations"));
    }

    fn range_query_count(&self, _start_key: &Key, _range: usize) -> Result<()> {
        return Err(anyhow!("Redis does not support range operations"));
    }

    fn range_delete(&self, _start_key: &Key, _end_key: &Key) -> Result<()> {
        return Err(anyhow!("Redis does not support range operations"));
    }

    fn range_delete_count(&self, _start_key: &Key, _range: usize) -> Result<()> {
        return Err(anyhow!("Redis does not support range operations"));
    }
}
