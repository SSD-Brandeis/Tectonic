use crate::{DBTranslationLayer, Key, Value};
use anyhow::{Result, anyhow};
use scylla::client::session::Session;
use scylla::client::session_builder::SessionBuilder;
use scylla::statement::prepared::PreparedStatement;
use std::str::from_utf8;
use tokio::runtime::{self, Runtime};

const KEYSPACE_NAME: &str = "tectonic";
const TABLE_NAME: &str = "tectonic.data";

pub struct Scylla {
    session: Session,
    runtime: runtime::Runtime,

    insert_statement: PreparedStatement,
    update_statement: PreparedStatement,
    point_query_statement: PreparedStatement,
    point_delete_statement: PreparedStatement,
    range_query_statement: Option<PreparedStatement>,
    range_query_count_statement: Option<PreparedStatement>,
    range_delete_statement: Option<PreparedStatement>,
}

impl Scylla {
    pub fn new(endpoint: Option<&str>, options: Option<&str>) -> Result<Self> {
        let runtime = Runtime::new()?;
        let endpoint = endpoint.unwrap_or("127.0.0.1:9042");
        let session = runtime.block_on(SessionBuilder::new().known_node(endpoint).build())?;

        let mut table_name = TABLE_NAME;
        if let Some(options) = options {
            let mut opt_iter = options.split(";");
            table_name = opt_iter.next().ok_or_else(|| anyhow!(
                "User defined options should start with a table name and contain at least one value"
            ))?;
            for opt in opt_iter {
                runtime
                    .block_on(session.query_unpaged(opt, ()))
                    .map_err(|e| anyhow!("Failed to run user setup query: {:?}", e))?;
            }
        } else {
            let query = format!(
                "CREATE KEYSPACE IF NOT EXISTS {KEYSPACE_NAME} WITH replication = {{'class': 'SimpleStrategy', 'replication_factor': 1}};"
            );
            runtime
                .block_on(session.query_unpaged(query, ()))
                .map_err(|e| anyhow!("Failed to create keyspace: {:?}", e))?;
            let query = format!(
                "CREATE TABLE IF NOT EXISTS {TABLE_NAME} (key text PRIMARY KEY, value text);"
            );
            runtime
                .block_on(session.query_unpaged(query, ()))
                .map_err(|e| anyhow!("Failed to create table: {:?}", e))?;
        }

        // Prepare Statements

        let query = format!("INSERT INTO {} (key, value) VALUES(?, ?);", table_name);
        let insert_statement = runtime
            .block_on(session.prepare(query))
            .map_err(|e| anyhow!("Scylla Error (failed to prepare statement): {:#?}", e))?;

        let query = format!("UPDATE {} SET value=? WHERE key=?;", table_name);
        let update_statement = runtime
            .block_on(session.prepare(query))
            .map_err(|e| anyhow!("Scylla Error (failed to prepare statement): {:#?}", e))?;

        let query = format!("DELETE FROM {} WHERE key=?;", table_name);
        let point_delete_statement = runtime
            .block_on(session.prepare(query))
            .map_err(|e| anyhow!("Scylla Error (failed to prepare statement): {:#?}", e))?;

        let query = format!("SELECT value FROM {} WHERE key=?;", table_name);
        let point_query_statement = runtime
            .block_on(session.prepare(query))
            .map_err(|e| anyhow!("Scylla Error (failed to prepare statement): {:#?}", e))?;

        let query = format!("SELECT value FROM {} WHERE key>=? AND key<?;", TABLE_NAME);
        let range_query_statement = runtime
            .block_on(session.prepare(query))
            .map_err(|e| anyhow!("Scylla Error (failed to prepare statement): {:#?}", e))
            .ok();

        let query = format!("SELECT value FROM {} WHERE key>=? LIMIT ?;", table_name);
        let range_query_count_statement = runtime
            .block_on(session.prepare(query))
            .map_err(|e| anyhow!("Scylla Error (failed to prepare statement): {:#?}", e))
            .ok();
        let query = format!("DELETE FROM {} WHERE key>=? AND key <?;", table_name);
        let range_delete_statement = runtime
            .block_on(session.prepare(query))
            .map_err(|e| anyhow!("Scylla Error (failed to prepare statement): {:#?}", e))
            .ok();

        return Ok(Self {
            session,
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

impl DBTranslationLayer for Scylla {
    fn cleanup(self) -> Result<()> {
        std::mem::drop(self);
        Ok(())
    }

    fn insert(&self, key: &Key, value: &Value) -> Result<()> {
        let key = from_utf8(key)?;
        let value = from_utf8(value)?;

        let _ = self
            .runtime
            .block_on(
                self.session
                    .execute_unpaged(&self.insert_statement, (key, value)),
            )
            .map_err(|e| anyhow!("Scylla Error: {:#?}", e))?;

        return Ok(());
    }

    fn update(&self, key: &Key, value: &Value) -> Result<()> {
        let key = from_utf8(key)?;
        let value = from_utf8(value)?;

        let _ = self
            .runtime
            .block_on(
                self.session
                    .execute_unpaged(&self.update_statement, (key, value)),
            )
            .map_err(|e| anyhow!("Scylla Error: {:#?}", e))?;
        return Ok(());
    }

    fn merge(&self, key: &Key, value: &Value) -> Result<()> {
        self.point_query(key)?;
        self.update(key, value)?;

        Ok(())
    }

    fn point_delete(&self, key: &Key) -> Result<()> {
        let key = from_utf8(key)?;

        let _ = self
            .runtime
            .block_on(
                self.session
                    .execute_unpaged(&self.point_delete_statement, (key,)),
            )
            .map_err(|e| anyhow!("Scylla Error: {:#?}", e))?;
        return Ok(());
    }

    fn point_query(&self, key: &Key) -> Result<()> {
        let key = from_utf8(key)?;

        let _ = self
            .runtime
            .block_on(
                self.session
                    .execute_unpaged(&self.point_query_statement, (key,)),
            )
            .map_err(|e| anyhow!("Scylla Error: {:#?}", e))?;
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

        let _ = self
            .runtime
            .block_on(
                self.session
                    .execute_unpaged(range_query_statement, (start_key, end_key)),
            )
            .map_err(|e| anyhow!("Scylla Error: {:#?}", e))?;
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

        let _ = self
            .runtime
            .block_on(
                self.session
                    .execute_unpaged(range_query_count_statement, (start_key, range as i64)),
            )
            .map_err(|e| anyhow!("Scylla Error: {:#?}", e))?;
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

        let _ = self
            .runtime
            .block_on(
                self.session
                    .execute_unpaged(range_delete_statement, (start_key, end_key)),
            )
            .map_err(|e| anyhow!("Scylla Error: {:#?}", e))?;
        return Ok(());
    }

    fn range_delete_count(&self, _start_key: &Key, _range: usize) -> Result<()> {
        eprintln!("[WARNING] Scylla does not support the range delete count operation");

        return Err(anyhow!(
            "Scylla does not support the range delete count operation"
        ));
    }
}
