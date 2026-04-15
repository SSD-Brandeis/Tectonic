use std::str::from_utf8;

use crate::{DBTranslationLayer, Key, Value};
use anyhow::{Result, anyhow};
use cassandra_cpp::{Cluster, PreparedStatement, Session};
use tokio::runtime::{self, Runtime};

const KEYSPACE_NAME: &str = "tectonic";
const TABLE_NAME: &str = "tectonic.data";

pub struct Cassandra {
    _session: Session,
    _cluster: Cluster,
    runtime: runtime::Runtime,

    insert_statement: PreparedStatement,
    update_statement: PreparedStatement,
    point_query_statement: PreparedStatement,
    point_delete_statement: PreparedStatement,
    range_query_statement: Option<PreparedStatement>,
    range_query_count_statement: Option<PreparedStatement>,
    range_delete_statement: Option<PreparedStatement>,
}

impl Cassandra {
    pub fn new(endpoint: Option<&str>, options: Option<&str>) -> Result<Self> {
        let runtime = Runtime::new()?;
        let mut cluster = Cluster::default();
        let mut endpoint = endpoint.unwrap_or("127.0.0.1");
        if let Some((ep, port)) = endpoint.split_once(':') {
            endpoint = ep;
            cluster
                .set_port(
                    port.parse::<u16>()
                        .map_err(|e| anyhow!("Could not parse port to an integer: {:#?}", e))?,
                )
                .map_err(|e| anyhow!("Couldn't set port for cassandra cluster: {:#?}", e))?;
        }
        cluster
            .set_contact_points(endpoint)
            .map_err(|e| anyhow!("Cassandra Error: {:#?}", e))?;
        let session = runtime
            .block_on(cluster.connect())
            .map_err(|e| anyhow!("Cassandra Error: {:#?}", e))?;

        let mut table_name = TABLE_NAME;
        if let Some(options) = options {
            let mut opt_iter = options.split(";");
            table_name = opt_iter.next().ok_or_else(|| anyhow!(
                "User defined options should start with a table name and contain at least one value"
            ))?;
            for opt in opt_iter {
                runtime
                    .block_on(session.execute(opt))
                    .map_err(|e| anyhow!("Failed to run user setup query: {:?}", e))?;
            }
        } else {
            let query = format!(
                "CREATE KEYSPACE IF NOT EXISTS {KEYSPACE_NAME} WITH replication = {{'class': 'SimpleStrategy', 'replication_factor': 1}};"
            );
            runtime
                .block_on(session.execute(query))
                .map_err(|e| anyhow!("Failed to create keyspace: {:?}", e))?;
            let query = format!(
                "CREATE TABLE IF NOT EXISTS {TABLE_NAME} (key text PRIMARY KEY, value text);"
            );
            runtime
                .block_on(session.execute(query))
                .map_err(|e| anyhow!("Failed to create table: {:?}", e))?;
        }

        // Prepare Statements

        let query = format!("INSERT INTO {} (key, value) VALUES(?, ?);", table_name);
        let insert_statement = runtime
            .block_on(session.prepare(query))
            .map_err(|e| anyhow!("Cassandra Error (failed to prepare statement): {:#?}", e))?;

        let query = format!("UPDATE {} SET value=? WHERE key=?;", table_name);
        let update_statement = runtime
            .block_on(session.prepare(query))
            .map_err(|e| anyhow!("Cassandra Error (failed to prepare statement): {:#?}", e))?;

        let query = format!("DELETE FROM {} WHERE key=?;", table_name);
        let point_delete_statement = runtime
            .block_on(session.prepare(query))
            .map_err(|e| anyhow!("Cassandra Error (failed to prepare statement): {:#?}", e))?;

        let query = format!("SELECT value FROM {} WHERE key=?;", table_name);
        let point_query_statement = runtime
            .block_on(session.prepare(query))
            .map_err(|e| anyhow!("Cassandra Error (failed to prepare statement): {:#?}", e))?;

        let query = format!("SELECT value FROM {} WHERE key>=? AND key<?;", TABLE_NAME);

        let range_query_statement = runtime
            .block_on(session.prepare(query))
            .map_err(|e| anyhow!("Cassandra Error (failed to prepare statement): {:#?}", e))
            .ok();

        let query = format!("SELECT value FROM {} WHERE key>=? LIMIT ?;", TABLE_NAME,);
        let range_query_count_statement = runtime
            .block_on(session.prepare(query))
            .map_err(|e| anyhow!("Cassandra Error (failed to prepare statement): {:#?}", e))
            .ok();
        let query = format!("DELETE FROM {} WHERE key>=? AND key <?;", TABLE_NAME,);
        let range_delete_statement = runtime
            .block_on(session.prepare(query))
            .map_err(|e| anyhow!("Cassandra Error (failed to prepare statement): {:#?}", e))
            .ok();

        return Ok(Self {
            _session: session,
            _cluster: cluster,
            runtime,

            insert_statement,
            update_statement,
            point_query_statement,
            point_delete_statement,
            range_query_statement,
            range_query_count_statement,
            range_delete_statement,
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
        let mut query = self.insert_statement.bind();
        query
            .bind_string(0, key)
            .map_err(|e| anyhow!("Cassandra failed to bind argument: {:#?}", e))?;
        query
            .bind_string(1, value)
            .map_err(|e| anyhow!("Cassandra failed to bind argument: {:#?}", e))?;
        let _ = self
            .runtime
            .block_on(query.execute())
            .map_err(|e| anyhow!("Cassandra Error: {:#?}", e))?;

        return Ok(());
    }

    fn update(&self, key: &Key, value: &Value) -> Result<()> {
        let key = from_utf8(key)?;
        let value = from_utf8(value)?;
        let mut query = self.update_statement.bind();
        query
            .bind_string(1, key)
            .map_err(|e| anyhow!("Cassandra failed to bind argument: {:#?}", e))?;
        query
            .bind_string(0, value)
            .map_err(|e| anyhow!("Cassandra failed to bind argument: {:#?}", e))?;

        let _ = self
            .runtime
            .block_on(query.execute())
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
        let mut query = self.point_delete_statement.bind();
        query
            .bind_string(0, key)
            .map_err(|e| anyhow!("Cassandra failed to bind argument: {:#?}", e))?;

        let _ = self
            .runtime
            .block_on(query.execute())
            .map_err(|e| anyhow!("Cassandra Error: {:#?}", e))?;
        return Ok(());
    }

    fn point_query(&self, key: &Key) -> Result<()> {
        let key = from_utf8(key)?;
        let mut query = self.point_query_statement.bind();
        query
            .bind_string(0, key)
            .map_err(|e| anyhow!("Cassandra failed to bind argument: {:#?}", e))?;

        let _ = self
            .runtime
            .block_on(query.execute())
            .map_err(|e| anyhow!("Cassandra Error: {:#?}", e))?;
        return Ok(());
    }

    fn range_query(&self, start_key: &Key, end_key: &Value) -> Result<()> {
        if self.range_query_statement.is_none() {
            // eprintln!(
            //     "[WARNING] Your current configuration of Cassandra does not support range queries. Skipping Operation"
            // );

            return Err(anyhow!(
                "Your current configuration of Cassandra does not support range queries"
            ));
        }

        let range_query_statement = self.range_query_statement.as_ref().unwrap();
        let start_key = from_utf8(start_key)?;
        let end_key = from_utf8(end_key)?;
        let mut query = range_query_statement.bind();

        query
            .bind_string(0, start_key)
            .map_err(|e| anyhow!("Cassandra failed to bind argument: {:#?}", e))?;
        query
            .bind_string(1, end_key)
            .map_err(|e| anyhow!("Cassandra failed to bind argument: {:#?}", e))?;

        let _ = self
            .runtime
            .block_on(query.execute())
            .map_err(|e| anyhow!("Cassandra Error: {:#?}", e))?;
        return Ok(());
    }

    fn range_query_count(&self, start_key: &Key, range: usize) -> Result<()> {
        if self.range_query_count_statement.is_none() {
            // eprintln!(
            //     "[WARNING] Your current configuration of Cassandra does not support range query count. Skipping Operation"
            // );

            return Err(anyhow!(
                "Your current configuration of Cassandra does not support range query count"
            ));
        }

        let range_query_count_statement = self.range_query_count_statement.as_ref().unwrap();
        let start_key = from_utf8(start_key)?;
        let mut query = range_query_count_statement.bind();

        query
            .bind_string(0, start_key)
            .map_err(|e| anyhow!("Cassandra failed to bind argument: {:#?}", e))?;
        query
            .bind_uint32(1, range as u32)
            .map_err(|e| anyhow!("Cassandra failed to bind argument: {:#?}", e))?;

        let _ = self
            .runtime
            .block_on(query.execute())
            .map_err(|e| anyhow!("Cassandra Error: {:#?}", e))?;
        return Ok(());
    }

    fn range_delete(&self, start_key: &Key, end_key: &Key) -> Result<()> {
        if self.range_delete_statement.is_none() {
            // eprintln!(
            //     "[WARNING] Your current configuration of Cassandra does not support range deletes. Skipping Operation"
            // );

            return Err(anyhow!(
                "Your current configuration of Cassandra does not support range deletes"
            ));
        }

        let range_delete_statement = self.range_delete_statement.as_ref().unwrap();
        let start_key = from_utf8(start_key)?;
        let end_key = from_utf8(end_key)?;
        let mut query = range_delete_statement.bind();
        query
            .bind_string(0, start_key)
            .map_err(|e| anyhow!("Cassandra failed to bind argument: {:#?}", e))?;
        query
            .bind_string(1, end_key)
            .map_err(|e| anyhow!("Cassandra failed to bind argument: {:#?}", e))?;

        let _ = self
            .runtime
            .block_on(query.execute())
            .map_err(|e| anyhow!("Cassandra Error: {:#?}", e))?;
        return Ok(());
    }

    fn range_delete_count(&self, _start_key: &Key, _range: usize) -> Result<()> {
        // eprintln!(
        //     "[WARNING] Cassandra does not support the range delete count operation. Skipping Operation"
        // );

        return Err(anyhow!(
            "Cassandra does not support the range delete count operation"
        ));
    }
}
