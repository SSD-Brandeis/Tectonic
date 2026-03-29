use std::{any::Any, str::from_utf8};

use crate::{DBTranslationLayer, Key, Value};
use anyhow::{Result, anyhow};
use cassandra_cpp::{Cluster, Session};
use tokio::runtime::{self, Runtime};

pub struct Cassandra {
    session: Session,
    cluster: Cluster,
    runtime: runtime::Runtime,
}

impl Cassandra {
    pub fn new(endpoint: Option<&str>, options: Option<&str>) -> Result<Self> {
        // if let Some(options) = options {
        //     let opt_iter = options.split(";");
        //     let keyspace_options = opt_iter.next();
        //
        //     let table_options = opt_iter.next();
        // }

        let runtime = Runtime::new()?;
        let mut cluster = Cluster::default();
        let endpoint =
            endpoint.ok_or_else(|| anyhow!("Cassandra requires at least one endpoint to work"))?;
        cluster
            .set_contact_points(endpoint)
            .map_err(|e| anyhow!("Cassandra Error: {:#?}", e))?;
        let session = runtime
            .block_on(cluster.connect())
            .map_err(|e| anyhow!("Cassandra Error: {:#?}", e))?;

        // TODO: Create new tectonic data table with passed in configuration options (if there are
        // any)
        let query = "CREATE KEYSPACE IF NOT EXISTS tectonic WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 1};";
        runtime
            .block_on(session.execute(query))
            .map_err(|e| anyhow!("Failed to create keyspace: {:?}", e))?;
        let query = "USE tectonic";
        runtime
            .block_on(session.execute(query))
            .map_err(|e| anyhow!("Failed to create keyspace: {:?}", e))?;
        let query = "CREATE TABLE IF NOT EXISTS tectonic.data (key text PRIMARY KEY, value text);";
        runtime
            .block_on(session.execute(query))
            .map_err(|e| anyhow!("Failed to create table: {:?}", e))?;

        return Ok(Self {
            session,
            cluster,
            runtime,
        });
    }
}

impl DBTranslationLayer for Cassandra {
    fn cleanup(self) -> Result<()> {
        std::mem::drop(self);
        Ok(())
    }

    fn insert(&self, key: &Key, value: &Value) -> Result<()> {
        let key = from_utf8(key)?;
        let value = from_utf8(value)?;
        let query = format!(
            "INSERT INTO Tectonic (key, value) VALUES('{}', '{}');",
            key, value
        );

        let _ = self
            .runtime
            .block_on(self.session.execute(query))
            .map_err(|e| anyhow!("Cassandra Error: {:#?}", e))?;

        return Ok(());
    }

    fn update(&self, key: &Key, value: &Value) -> Result<()> {
        let key = from_utf8(key)?;
        let value = from_utf8(value)?;
        let query = format!("UPDATE Tectonic SET value='{}' WHERE key='{}';", value, key);

        let _ = self
            .runtime
            .block_on(self.session.execute(query))
            .map_err(|e| anyhow!("Cassandra Error: {:#?}", e))?;
        return Ok(());
    }

    fn merge(&self, key: &Key, value: &Value) -> Result<()> {
        self.point_query(key)?;
        self.update(key, value)?;

        Ok(())
    }

    fn point_delete(&self, key: &Key) -> Result<()> {
        let key = from_utf8(key)?;
        let query = format!("DELETE FROM Tectonic WHERE key='{}';", key);

        let _ = self
            .runtime
            .block_on(self.session.execute(query))
            .map_err(|e| anyhow!("Cassandra Error: {:#?}", e))?;
        return Ok(());
    }

    fn point_query(&self, key: &Key) -> Result<()> {
        let key = from_utf8(key)?;
        let query = format!("SELECT value FROM Tectonic WHERE key='{}';", key);

        let _ = self
            .runtime
            .block_on(self.session.execute(query))
            .map_err(|e| anyhow!("Cassandra Error: {:#?}", e))?;
        return Ok(());
    }

    fn range_query(&self, start_key: &Key, end_key: &Value) -> Result<()> {
        let start_key = from_utf8(start_key)?;
        let end_key = from_utf8(end_key)?;
        let query = format!(
            "SELECT value FROM Tectonic WHERE key>='{}' AND key<'{}';",
            start_key, end_key
        );

        let _ = self
            .runtime
            .block_on(self.session.execute(query))
            .map_err(|e| anyhow!("Cassandra Error: {:#?}", e))?;
        return Ok(());
    }

    fn range_query_count(&self, start_key: &Key, range: usize) -> Result<()> {
        let start_key = from_utf8(start_key)?;
        let query = format!(
            "SELECT value FROM Tectonic WHERE key>='{}' LIMIT {};",
            start_key, range
        );

        let _ = self
            .runtime
            .block_on(self.session.execute(query))
            .map_err(|e| anyhow!("Cassandra Error: {:#?}", e))?;
        return Ok(());
    }

    fn range_delete(&self, start_key: &Key, end_key: &Key) -> Result<()> {
        let start_key = from_utf8(start_key)?;
        let end_key = from_utf8(end_key)?;
        let query = format!(
            "DELETE FROM Tectonic WHERE key>='{}' AND key <'{}';",
            start_key, end_key
        );

        let _ = self
            .runtime
            .block_on(self.session.execute(query))
            .map_err(|e| anyhow!("Cassandra Error: {:#?}", e))?;
        return Ok(());
    }

    fn range_delete_count(&self, _start_key: &Key, _range: usize) -> Result<()> {
        eprintln!("[WARNING] Cassandra does not support the range delete count operation");

        return Err(anyhow!(
            "Cassandra does not support the range delete count operation"
        ));
    }
}
